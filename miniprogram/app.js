// app.js
App({
  onLaunch() {
    console.log('局域网自助打印小程序启动');
  },
  globalData: {
    defaultServerUrl: 'http://192.168.10.66:5005'
  }
});
