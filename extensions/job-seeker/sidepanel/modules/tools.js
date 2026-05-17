/*
 * sidepanel/modules/tools.js — 「工具」tab 控制器 + 二级菜单
 *
 * 工具 tab 下挂二级菜单：表单填写 / 表单资料 / 填写记录 / API录制。
 * 前三项来自合并的 autofill 子系统（window.AF.*），第四项是 API 录制视图。
 *
 * v3 登录身份直接复用（AF.state.auth ← DQ.state.user），不再单独登录。
 */
(function () {
  'use strict';
  const { NAMES, on } = window.DQ.events;
  window.AF = window.AF || {};
  AF.state = AF.state || { profile: {}, lastPage: null, auth: null };

  const SUBS = ['detect', 'log', 'recorder'];   // 表单资料已并入「我的」tab
  let current = 'detect';
  let inited = false;

  function $(id) { return document.getElementById(id); }

  function switchSub(name) {
    if (!SUBS.includes(name)) return;
    if (current === 'recorder' && name !== 'recorder') {
      window.DQ.recorderView && window.DQ.recorderView.deactivate &&
        window.DQ.recorderView.deactivate();
    }
    current = name;
    document.querySelectorAll('#toolsSubnav .subnav-btn').forEach((b) => {
      b.classList.toggle('active', b.dataset.sub === name);
    });
    SUBS.forEach((s) => {
      const v = $('view-' + s);
      if (v) v.classList.toggle('hidden', s !== name);
    });
    renderSub(name);
  }

  function renderSub(name) {
    if (name === 'detect') { AF.detect && AF.detect.render && AF.detect.render(); }
    else if (name === 'log') { AF.log && AF.log.render && AF.log.render(); }
    else if (name === 'recorder') {
      window.DQ.recorderView && window.DQ.recorderView.activate &&
        window.DQ.recorderView.activate();
    }
  }

  async function lazyInit() {
    if (inited) return;
    inited = true;
    // 复用 v3 登录身份做账号显示
    const u = (window.DQ.state && window.DQ.state.user) || {};
    AF.state.auth = { user: { id: u.id || '', email: u.email || '' } };
    try {
      const r = await AF.api.getProfile();
      if (r && r.ok) AF.state.profile = r.profile || {};
    } catch (e) { /* 本地兜底 */ }
    try { AF.detect && AF.detect.init && AF.detect.init(); } catch (e) {}
    try { if (AF.log && AF.log.init) await AF.log.init(); } catch (e) {}
  }

  // 二级菜单点击
  const nav = $('toolsSubnav');
  if (nav) {
    nav.addEventListener('click', (e) => {
      const btn = e.target.closest('.subnav-btn[data-sub]');
      if (btn) switchSub(btn.dataset.sub);
    });
  }

  // 工具 tab 打开 → 懒初始化 + 渲染当前子页；离开 → 停录制视图轮询
  on(NAMES.TAB_CHANGED, async (e) => {
    if (!e.detail || e.detail.tab !== 'tools') {
      window.DQ.recorderView && window.DQ.recorderView.deactivate &&
        window.DQ.recorderView.deactivate();
      return;
    }
    await lazyInit();
    renderSub(current);
  });

  window.DQ = window.DQ || {};
  window.DQ.tools = { switchSub };
})();
