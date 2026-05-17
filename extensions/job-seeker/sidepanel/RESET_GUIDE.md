# 求职者引导重置方法

## 方法 1：通过界面操作

1. 点击侧边栏底部的「我的」Tab
2. 点击「求职者工作台」进入工作台
3. 在工作台中可以上传简历和设置偏好

## 方法 2：通过浏览器控制台

打开扩展的侧边栏，然后按 F12 打开开发者工具，在 Console 中运行：

```javascript
// 只重置简历（保留偏好设置）
window.DQ.jobseeker.resetWizard();

// 完全重置（清除所有设置）
window.DQ.jobseeker.resetAll();
```

## 方法 3：清除存储数据

在控制台运行：

```javascript
chrome.storage.local.get(['jobseeker'], (r) => {
  console.log('当前求职者数据:', r.jobseeker);
});

// 清除求职者数据
chrome.storage.local.set({ jobseeker: {} }, () => {
  console.log('已清除求职者数据，请刷新侧边栏');
  location.reload();
});
```

## 验证当前状态

在控制台运行以下命令查看当前状态：

```javascript
// 查看所有存储数据
chrome.storage.local.get(null, (data) => console.log(data));

// 只查看求职者相关数据
chrome.storage.local.get(['jobseeker', 'user'], (data) => console.log(data));

// 检查角色
window.DQ.state.role; // 应该是 'jobseeker'
```
