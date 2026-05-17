/*! DingQ i18n 词典 —— 通用(跨界面复用的按钮 / 状态文案) */
;(function () {
  var ns = {
    zh: {
      'common.confirm': '确定',
      'common.cancel': '取消',
      'common.save': '保存',
      'common.delete': '删除',
      'common.edit': '编辑',
      'common.close': '关闭',
      'common.back': '返回',
      'common.ok': '好的',
      'common.loading': '加载中…',
      'common.retry': '重试',
      'common.send': '发送',
      'common.search': '搜索',
      'common.copy': '复制',
      'common.copied': '已复制',
      'common.refresh': '刷新',
      'common.empty': '暂无数据',
      'common.platformBoss': 'Boss直聘'
    },
    en: {
      'common.confirm': 'OK',
      'common.cancel': 'Cancel',
      'common.save': 'Save',
      'common.delete': 'Delete',
      'common.edit': 'Edit',
      'common.close': 'Close',
      'common.back': 'Back',
      'common.ok': 'OK',
      'common.loading': 'Loading…',
      'common.retry': 'Retry',
      'common.send': 'Send',
      'common.search': 'Search',
      'common.copy': 'Copy',
      'common.copied': 'Copied',
      'common.refresh': 'Refresh',
      'common.empty': 'No data',
      'common.platformBoss': 'Boss'
    }
  };
  if (window.DQI18N) window.DQI18N.register(ns);
  else (window.__DQ_I18N__ = window.__DQ_I18N__ || []).push(ns);
})();
