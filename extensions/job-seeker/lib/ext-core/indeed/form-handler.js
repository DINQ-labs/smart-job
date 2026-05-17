/**
 * indeed/form-handler.js — Indeed Apply 多步表单编排器
 *
 * 使用 core/form-filler.js 原语在 MAIN world 中填写 Indeed 申请表单。
 * 结构与 linkedin/form-handler.js 对称，复用通用层。
 *
 * 依赖（全局）：executeFormActionOnTab, navigateSiteWorkerTab, waitForSiteTabLoad,
 *   formGetStructure, formFillByActions, formClickElement, formWaitForElement,
 *   formUploadFile
 */

// ── 已知字段映射表 ───────────────────────────────────────────────────────

const INDEED_KNOWN_FIELDS = {
  'email': 'email',
  'email address': 'email',
  'first name': 'firstName',
  'last name': 'lastName',
  'full name': 'fullName',
  'phone': 'phone',
  'phone number': 'phone',
  'city': 'city',
  'location': 'city',
  'address': 'address',
  'zip code': 'zipCode',
  'postal code': 'zipCode',
  'state': 'state',
  'country': 'country',
};

const INDEED_FUZZY_FIELDS = [
  { keyword: 'years of experience', field: 'experienceYears' },
  { keyword: 'work authorization', field: 'workAuthorization' },
  { keyword: 'authorized to work', field: 'workAuthorization' },
  { keyword: 'salary', field: 'expectedSalary' },
  { keyword: 'start date', field: 'startDate' },
  { keyword: 'education', field: 'education' },
  { keyword: 'degree', field: 'degree' },
];

const INDEED_FILE_KEYWORDS = ['resume', 'cv', 'cover letter'];

/**
 * 根据 label 匹配 profileData 字段值。
 */
function _matchIndeedField(label, type, profileData) {
  if (!label || !profileData) return { matched: false };
  const labelLower = label.toLowerCase().trim();

  if (type === 'file' || INDEED_FILE_KEYWORDS.some(k => labelLower.includes(k))) {
    if (profileData.resumeBase64) {
      return { matched: true, value: '__FILE_UPLOAD__', fileField: true };
    }
    return { matched: false };
  }

  const exactField = INDEED_KNOWN_FIELDS[labelLower];
  if (exactField && profileData[exactField] !== undefined && profileData[exactField] !== '') {
    return { matched: true, value: String(profileData[exactField]) };
  }

  for (const { keyword, field } of INDEED_FUZZY_FIELDS) {
    if (labelLower.includes(keyword) && profileData[field] !== undefined && profileData[field] !== '') {
      return { matched: true, value: String(profileData[field]) };
    }
  }

  return { matched: false };
}

/**
 * Indeed Apply 完整多步表单编排。
 *
 * @param {number} tabId - 职位详情页标签页 ID
 * @param {string} jobId - 职位 ID（jobKey）
 * @param {object} profileData - 用户资料数据
 * @returns {Promise<{ ok: boolean, status: string, steps: number, unresolved?: Array }>}
 */
async function indeedApply(tabId, jobId, profileData) {
  const MAX_STEPS = 10;
  let steps = 0;
  const allUnresolved = [];

  // Step 1: 点击 Apply 按钮 (Sprint 4 / E4: 走人类化 hover+mousedown+up+click)
  const clickResult = await executeFormActionOnTab(tabId, formHumanClick,
    '[data-testid="indeedApplyButton"], .indeed-apply-button, #indeedApplyButton, button[id*="apply"]',
    8000
  );
  if (!clickResult.ok) {
    return { ok: false, status: 'apply_button_not_found', error: clickResult.error, steps: 0 };
  }

  // 等待申请表单出现
  const formResult = await executeFormActionOnTab(tabId, formWaitForElement,
    '.ia-BasePage, .indeed-apply-widget, [data-testid="apply-form"], form', 8000
  );
  if (!formResult.found) {
    // 可能外跳到第三方 ATS
    return { ok: false, status: 'external_ats_redirect', error: '可能跳转到外部申请系统', steps: 0 };
  }

  // Step 2-N: 循环处理每一步
  while (steps < MAX_STEPS) {
    steps++;
    await _indeedSleep(500);

    const structure = await executeFormActionOnTab(tabId, formGetStructure,
      '.ia-BasePage, .indeed-apply-widget, [data-testid="apply-form"], form'
    );

    if (!structure.fields || structure.fields.length === 0) {
      const hasSubmit = structure.buttons && structure.buttons.submit;
      if (hasSubmit) {
        const submitResult = await executeFormActionOnTab(tabId, formHumanClick,
          structure.buttons.submit, 5000
        );
        if (submitResult.ok) {
          return {
            ok: true,
            status: 'submitted',
            steps,
            unresolvedCount: allUnresolved.length,
            unresolved: allUnresolved.length > 0 ? allUnresolved : undefined,
          };
        }
      }
      break;
    }

    const resolved = [];
    const stepUnresolved = [];

    for (const field of structure.fields) {
      if (field.currentValue && field.currentValue.trim()) continue;

      const match = _matchIndeedField(field.label || field.ariaLabel, field.type, profileData);
      if (match.matched) {
        resolved.push({ selector: field.selector, value: match.value, type: field.type, fileField: match.fileField });
      } else {
        stepUnresolved.push({
          selector: field.selector,
          label: field.label || field.ariaLabel,
          type: field.type,
          required: field.required,
          options: field.options,
          name: field.name,
        });
      }
    }

    if (resolved.length > 0) {
      const fillActions = [];
      for (const r of resolved) {
        if (r.fileField && profileData.resumeBase64) {
          await executeFormActionOnTab(tabId, formUploadFile,
            r.selector,
            profileData.resumeBase64,
            profileData.resumeFileName || 'resume.pdf',
            profileData.resumeMimeType || 'application/pdf'
          );
        } else if (!r.fileField) {
          fillActions.push({ selector: r.selector, value: r.value, type: r.type });
        }
      }
      if (fillActions.length > 0) {
        await executeFormActionOnTab(tabId, formFillByActionsHuman, fillActions);
      }
    }

    if (stepUnresolved.length > 0) {
      const requiredUnresolved = stepUnresolved.filter(f => f.required);
      if (requiredUnresolved.length > 0) {
        allUnresolved.push(...stepUnresolved);
        return {
          ok: false,
          status: 'unresolved_fields',
          step: steps,
          resolved: resolved.map(r => r.selector),
          unresolved: stepUnresolved,
          buttons: structure.buttons,
          steps,
        };
      }
      allUnresolved.push(...stepUnresolved);
    }

    const btnOrder = ['submit', 'review', 'next'];
    let clicked = false;
    for (const key of btnOrder) {
      if (structure.buttons[key]) {
        const btnResult = await executeFormActionOnTab(tabId, formHumanClick,
          structure.buttons[key], 5000
        );
        if (btnResult.ok) {
          clicked = true;
          if (key === 'submit') {
            await _indeedSleep(1000);
            return {
              ok: true,
              status: 'submitted',
              steps,
              unresolvedCount: allUnresolved.length,
              unresolved: allUnresolved.length > 0 ? allUnresolved : undefined,
            };
          }
          break;
        }
      }
    }

    if (!clicked) {
      return { ok: false, status: 'no_navigation_button', steps, error: '找不到 Next/Submit 按钮' };
    }

    await _indeedSleep(800);
  }

  return {
    ok: false,
    status: 'max_steps_exceeded',
    steps,
    error: `超过最大步骤数 ${MAX_STEPS}`,
    unresolved: allUnresolved.length > 0 ? allUnresolved : undefined,
  };
}

async function indeedGetApplyForm(tabId) {
  return await executeFormActionOnTab(tabId, formGetStructure,
    '.ia-BasePage, .indeed-apply-widget, [data-testid="apply-form"], form'
  );
}

async function indeedFillFields(tabId, actions) {
  return await executeFormActionOnTab(tabId, formFillByActionsHuman, actions);
}

function _indeedSleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}
