(function () {
  document.getElementById('openPanel')?.addEventListener('click', async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.windowId) chrome.sidePanel.open({ windowId: tab.windowId });
    window.close();
  });
  document.getElementById('openOptions')?.addEventListener('click', () => {
    chrome.runtime.openOptionsPage();
    window.close();
  });
})();
