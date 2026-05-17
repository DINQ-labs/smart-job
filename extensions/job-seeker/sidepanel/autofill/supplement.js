/*
 * modules/supplement.js — 缺字段补充表单
 *
 * 检测后匹配不上的字段在此让用户补充，提交后由 detect.js 保存到 profile 并填写。
 */
(function () {
  'use strict';
  window.AF = window.AF || {};
  const esc = (s) => AF.esc(s);

  function inputFor(f) {
    if ((f.type === 'select' || f.type === 'radio') && f.options) {
      return `<select class="input" data-sup="${esc(f.fid)}">
        <option value="">${t('autofill.selectPlaceholder')}</option>
        ${f.options.map((o) => `<option value="${esc(o.value)}">${esc(o.text)}</option>`).join('')}
      </select>`;
    }
    if (f.type === 'textarea') {
      return `<textarea class="input" data-sup="${esc(f.fid)}" rows="3"></textarea>`;
    }
    if (f.type === 'checkbox') {
      return `<select class="input" data-sup="${esc(f.fid)}">
        <option value="">${t('autofill.selectPlaceholder')}</option>
        <option value="true">${t('autofill.checkboxYes')}</option>
        <option value="false">${t('autofill.checkboxNo')}</option>
      </select>`;
    }
    return `<input class="input" data-sup="${esc(f.fid)}" type="text"
      placeholder="${esc(f.placeholder || '')}">`;
  }

  /**
   * @param {HTMLElement} hostEl
   * @param {Array} fields    未匹配字段（含 selector/type/options）
   * @param {(values:Array<{fid,value}>)=>void} onSubmit
   */
  function render(hostEl, fields, onSubmit) {
    if (!hostEl) return;
    if (!fields || !fields.length) { hostEl.innerHTML = ''; return; }

    const rows = fields.map((f) => `
      <div class="sup-row">
        <label class="sup-label">
          ${esc(f.label || f.name || f.fid)}${f.required ? ' <span class="req">*</span>' : ''}
        </label>
        ${inputFor(f)}
      </div>
    `).join('');

    hostEl.innerHTML = `
      <div class="card card-amber">
        <div class="card-title">${t('autofill.supplementTitle', { n: fields.length })}</div>
        <div class="hint">${t('autofill.supplementHint')}</div>
        ${rows}
        <button class="btn btn-primary btn-block" id="af-sup-btn">${t('autofill.supplementSaveBtn')}</button>
      </div>
    `;

    hostEl.querySelector('#af-sup-btn').addEventListener('click', () => {
      const values = fields.map((f) => {
        const inp = hostEl.querySelector('[data-sup="' + CSS.escape(f.fid) + '"]');
        return { fid: f.fid, value: inp ? inp.value.trim() : '' };
      }).filter((v) => v.value);
      if (!values.length) { AF.toast(t('autofill.supplementEmpty'), 'warn'); return; }
      onSubmit(values);
    });
  }

  AF.supplement = { render };
})();
