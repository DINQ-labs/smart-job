/**
 * linkedin/form-handler.js — LinkedIn Easy Apply 多步表单编排器
 *
 * 使用 core/form-filler.js 原语在 MAIN world 中填写 Easy Apply 模态框。
 * 支持混合模式：已知字段本地直填，未知字段回传给 Gateway AI 推理。
 *
 * 依赖（全局）：executeFormActionOnTab, navigateSiteWorkerTab, waitForSiteTabLoad,
 *   formGetStructure, formFillInput, formFillTextarea, formSelectNative,
 *   formClickCustomDropdown, formCheckRadio, formClickElement, formWaitForElement,
 *   formUploadFile, formFillByActions
 * Sprint 4 / E4: click + fill 改走 *Human 变体（依赖 window.DQ_Human，
 * 由 site-executor 自动注入 ext_shared/core/human-simulator.js）。
 */

// ── 已知字段映射表 ───────────────────────────────────────────────────────
// key: label 小写化后文本（精确或模糊匹配），value: profileData 中的字段名

const LINKEDIN_KNOWN_FIELDS = {
  // 精确匹配
  'email address': 'email',
  'email': 'email',
  'first name': 'firstName',
  'last name': 'lastName',
  'full name': 'fullName',
  'phone number': 'phone',
  'mobile phone number': 'phone',
  'phone': 'phone',
  'city': 'city',
  'location': 'city',
  'headline': 'headline',
  'linkedin profile': 'linkedinUrl',
  'website': 'website',
  'address': 'address',
  'street address': 'address',
  'zip': 'zipCode',
  'zip code': 'zipCode',
  'postal code': 'zipCode',
  'state': 'state',
  'country': 'country',
};

// 模糊匹配（label 中 contains 这些关键词即匹配）
const LINKEDIN_FUZZY_FIELDS = [
  { keyword: 'years of experience', field: 'experienceYears' },
  { keyword: 'work authorization', field: 'workAuthorization' },
  { keyword: 'authorized to work', field: 'workAuthorization' },
  { keyword: 'require sponsorship', field: 'requireSponsorship' },
  { keyword: 'visa sponsorship', field: 'requireSponsorship' },
  { keyword: 'salary', field: 'expectedSalary' },
  { keyword: 'desired salary', field: 'expectedSalary' },
  { keyword: 'start date', field: 'startDate' },
  { keyword: 'how did you hear', field: 'referralSource' },
  { keyword: 'gender', field: 'gender' },
  { keyword: 'race', field: 'race' },
  { keyword: 'ethnicity', field: 'ethnicity' },
  { keyword: 'veteran', field: 'veteranStatus' },
  { keyword: 'disability', field: 'disabilityStatus' },
];

// 文件上传字段
const LINKEDIN_FILE_KEYWORDS = ['resume', 'cv', 'cover letter'];

/**
 * 根据 label 匹配 profileData 中的字段值。
 * @param {string} label - 字段 label
 * @param {string} type - 字段类型
 * @param {object} profileData - 用户资料
 * @returns {{ matched: boolean, value?: string }}
 */
function _matchField(label, type, profileData) {
  if (!label || !profileData) return { matched: false };
  const labelLower = label.toLowerCase().trim();

  // 文件上传检测
  if (type === 'file' || LINKEDIN_FILE_KEYWORDS.some(k => labelLower.includes(k))) {
    if (profileData.resumeBase64) {
      return { matched: true, value: '__FILE_UPLOAD__', fileField: true };
    }
    return { matched: false };
  }

  // 精确匹配
  const exactField = LINKEDIN_KNOWN_FIELDS[labelLower];
  if (exactField && profileData[exactField] !== undefined && profileData[exactField] !== '') {
    return { matched: true, value: String(profileData[exactField]) };
  }

  // 模糊匹配
  for (const { keyword, field } of LINKEDIN_FUZZY_FIELDS) {
    if (labelLower.includes(keyword) && profileData[field] !== undefined && profileData[field] !== '') {
      return { matched: true, value: String(profileData[field]) };
    }
  }

  return { matched: false };
}

/**
 * LinkedIn Easy Apply 完整多步表单编排。
 *
 * @param {number} tabId - 职位详情页标签页 ID
 * @param {string} jobId - 职位 ID
 * @param {object} profileData - 用户资料数据
 * @returns {Promise<{ ok: boolean, status: string, steps: number, unresolved?: Array }>}
 */
async function linkedinEasyApply(tabId, jobId, profileData) {
  const MAX_STEPS = 10;
  let steps = 0;
  const allUnresolved = [];

  // Step 1: 点击 Easy Apply 按钮
  const clickResult = await executeFormActionOnTab(tabId, formHumanClick,
    '.jobs-apply-button--top-card, .jobs-s-apply button, button[aria-label*="Easy Apply"]',
    8000
  );
  if (!clickResult.ok) {
    return { ok: false, status: 'apply_button_not_found', error: clickResult.error, steps: 0 };
  }

  // 等待模态框出现
  const modalResult = await executeFormActionOnTab(tabId, formWaitForElement,
    '.jobs-easy-apply-content, .jobs-easy-apply-modal', 8000
  );
  if (!modalResult.found) {
    return { ok: false, status: 'modal_not_found', error: '模态框未出现', steps: 0 };
  }

  // Step 2-N: 循环处理每一步
  while (steps < MAX_STEPS) {
    steps++;
    await _sleep(500); // 等待表单渲染

    // 读取当前步骤的表单结构
    const structure = await executeFormActionOnTab(tabId, formGetStructure,
      '.jobs-easy-apply-content'
    );

    if (!structure.fields || structure.fields.length === 0) {
      // 可能是确认页或已提交
      const hasSubmit = structure.buttons && structure.buttons.submit;
      if (hasSubmit) {
        // 点击提交
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
      // 没有字段也没有提交按钮，可能已完成
      break;
    }

    // 匹配并填写字段
    const resolved = [];
    const stepUnresolved = [];

    for (const field of structure.fields) {
      // 跳过已有值的字段
      if (field.currentValue && field.currentValue.trim()) continue;

      const match = _matchField(field.label || field.ariaLabel, field.type, profileData);
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

    // 执行填写（已知字段）
    if (resolved.length > 0) {
      const fillActions = [];
      for (const r of resolved) {
        if (r.fileField && profileData.resumeBase64) {
          // 文件上传单独处理
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

    // 有未识别字段 → 暂停，返回给 Gateway
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
      // 非必填的未识别字段记录但不阻塞
      allUnresolved.push(...stepUnresolved);
    }

    // 点击 Next / Review / Submit
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
            await _sleep(1000);
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

    // 等待下一步加载
    await _sleep(800);
  }

  return {
    ok: false,
    status: 'max_steps_exceeded',
    steps,
    error: `超过最大步骤数 ${MAX_STEPS}`,
    unresolved: allUnresolved.length > 0 ? allUnresolved : undefined,
  };
}

/**
 * 在指定标签页上获取 LinkedIn Easy Apply 表单结构（不填写）。
 * @param {number} tabId
 * @returns {Promise<{ fields: Array, buttons: object }>}
 */
async function linkedinGetApplyForm(tabId) {
  return await executeFormActionOnTab(tabId, formGetStructure, '.jobs-easy-apply-content');
}

/**
 * 在指定标签页上按指令填写字段。
 * @param {number} tabId
 * @param {Array<{ selector: string, value: string, type?: string }>} actions
 * @returns {Promise<{ ok: boolean, filled: number, failed: Array }>}
 */
async function linkedinFillFields(tabId, actions) {
  return await executeFormActionOnTab(tabId, formFillByActionsHuman, actions);
}

/** 简单延迟工具 */
function _sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}
