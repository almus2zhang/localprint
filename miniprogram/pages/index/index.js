// pages/index/index.js

const DEFAULT_SERVER_URL = 'http://192.168.10.66:5005';
const DOC_EXTENSIONS = ['doc', 'docx', 'pdf', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv'];
const ARCHIVE_EXTENSIONS = ['zip', 'rar', '7z', 'tar', 'gz', 'bz2'];

Page({
  data: {
    serverUrl: DEFAULT_SERVER_URL,
    serverConnected: false,
    checkingServer: false,
    // 弹窗状态
    showEditModal: false,
    showArchiveModal: false,
    showQqGuideModal: false,
    tempServerUrl: DEFAULT_SERVER_URL,
    
    // 多文件待打印列表（每个文件包含独立的 printType, copies, pageRanges）
    selectedFiles: [],
    uploading: false,

    // 压缩包解析与文件挑选
    archiveFiles: [],
    selectedArchiveCount: 0,
    isAllArchiveSelected: true,
    archiveCopies: 1,
    archivePrintTypeIndex: 0,
    printTypeOptions: ['双面长边(左右翻)', '双面短边(上下翻)', '单面打印'],
    printTypeValues: ['two-sided-long-edge', 'two-sided-short-edge', 'one-sided'],

    // 打印历史（按天折叠展示）
    dateGroups: [],
    totalHistoryCount: 0
  },

  onLoad() {
    const savedUrl = wx.getStorageSync('serverUrl');
    const activeUrl = savedUrl || DEFAULT_SERVER_URL;
    this.setData({
      serverUrl: activeUrl,
      tempServerUrl: activeUrl
    });
    this.checkServerConnection();
    this.fetchHistory();
  },

  // 格式化文件大小
  formatFileSize(bytes) {
    if (!bytes || isNaN(bytes)) return '未知大小';
    const sizeKB = (bytes / 1024).toFixed(1);
    if (sizeKB > 1024) {
      return (sizeKB / 1024).toFixed(2) + ' MB';
    }
    return sizeKB + ' KB';
  },

  // 获取文件扩展名
  getFileExt(filename) {
    if (!filename || !filename.includes('.')) return '';
    return filename.split('.').pop().toLowerCase();
  },

  // 获取文件图标
  getFileIcon(filename) {
    const ext = this.getFileExt(filename);
    if (['doc', 'docx'].includes(ext)) return '📝';
    if (['xls', 'xlsx', 'csv'].includes(ext)) return '📊';
    if (['ppt', 'pptx'].includes(ext)) return '📑';
    if (ext === 'pdf') return '📄';
    if (['jpg', 'jpeg', 'png', 'bmp', 'webp'].includes(ext)) return '🖼️';
    return '📄';
  },

  // 打开修改服务器地址弹窗
  openEditModal() {
    this.setData({
      showEditModal: true,
      tempServerUrl: this.data.serverUrl
    });
  },

  closeEditModal() {
    this.setData({ showEditModal: false });
  },

  // 打开/关闭 QQ 文件导入指引弹窗
  openQqGuideModal() {
    this.setData({ showQqGuideModal: true });
  },

  closeQqGuideModal() {
    this.setData({ showQqGuideModal: false });
  },

  // 复制打印机网页端地址
  copyWebUrl() {
    wx.setClipboardData({
      data: this.data.serverUrl,
      success: () => {
        wx.showToast({ title: '地址已复制，可在手机浏览器打开', icon: 'none' });
      }
    });
  },

  preventBubble() {},

  onTempUrlInput(e) {
    this.setData({ tempServerUrl: e.detail.value.trim() });
  },

  resetToDefaultUrl() {
    this.setData({ tempServerUrl: DEFAULT_SERVER_URL });
  },

  saveServerUrl() {
    let url = this.data.tempServerUrl.trim();
    if (!url) {
      wx.showToast({ title: '地址不能为空', icon: 'none' });
      return;
    }
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = 'http://' + url;
    }
    this.setData({
      serverUrl: url,
      showEditModal: false
    });
    wx.setStorageSync('serverUrl', url);
    this.checkServerConnection();
    this.fetchHistory();
  },

  // 测试与服务端的连通性
  checkServerConnection() {
    const url = this.data.serverUrl;
    if (!url) return;

    this.setData({ checkingServer: true });
    wx.showLoading({ title: '检测服务中...' });
    wx.request({
      url: `${url}/api/status`,
      method: 'GET',
      timeout: 3500,
      success: (res) => {
        wx.hideLoading();
        this.setData({ checkingServer: false });
        if (res.statusCode === 200 && res.data && res.data.status === 'online') {
          this.setData({ serverConnected: true });
          wx.showToast({ title: '已连上打印机', icon: 'success' });
          this.fetchQueue();
        } else {
          this.setData({ serverConnected: false });
          wx.showToast({ title: '服务响应异常', icon: 'none' });
        }
      },
      fail: (err) => {
        wx.hideLoading();
        this.setData({
          checkingServer: false,
          serverConnected: false
        });
        wx.showModal({
          title: '连接失败',
          content: '无法连接到打印系统，请检查：\n1. 手机是否连入相同Wi-Fi\n2. 打印机IP端口是否正确\n3. 微信右上角...是否开启【开发调试】',
          showCancel: false
        });
      }
    });
  },

  // --- 多选文件添加辅助方法 ---
  appendFiles(newFileList) {
    const currentList = this.data.selectedFiles || [];
    const existingPaths = new Set(currentList.map(f => f.path));
    const addedItems = [];

    newFileList.forEach((f, idx) => {
      if (!existingPaths.has(f.path)) {
        addedItems.push({
          id: `f_${Date.now()}_${idx}_${Math.random().toString(36).substr(2, 4)}`,
          path: f.path,
          name: f.name,
          sizeText: this.formatFileSize(f.size),
          icon: this.getFileIcon(f.name),
          // 每个文档独立的初始打印参数
          printType: 'two-sided-long-edge',
          copies: 1,
          pageRanges: ''
        });
      }
    });

    if (addedItems.length === 0) {
      wx.showToast({ title: '所选文件已在清单中', icon: 'none' });
      return;
    }

    this.setData({
      selectedFiles: [...currentList, ...addedItems]
    });
    wx.showToast({ title: `成功添加 ${addedItems.length} 个文档`, icon: 'success' });
  },

  // --- 微信聊天记录分类选择（支持一次多选高达20个，秒开无卡顿） ---

  // 1. 聊天记录 - 多选文档
  chooseChatDoc() {
    wx.chooseMessageFile({
      count: 20, // 支持多选！
      type: 'file', // 秒开
      success: (res) => {
        if (!res.tempFiles || res.tempFiles.length === 0) return;
        const validFiles = res.tempFiles.filter(f => DOC_EXTENSIONS.includes(this.getFileExt(f.name)));
        
        if (validFiles.length === 0) {
          wx.showModal({
            title: '无有效文档',
            content: '所选文件均非 Word/PDF/Excel/PPT/TXT 文档，请重新选择。',
            showCancel: false
          });
          return;
        }

        if (validFiles.length < res.tempFiles.length) {
          wx.showToast({ title: '已自动过滤不支持的文件', icon: 'none' });
        }

        this.appendFiles(validFiles);
      }
    });
  },

  // 2. 聊天记录 - 多选图片
  chooseChatImage() {
    wx.chooseMessageFile({
      count: 20, // 支持多选！
      type: 'image',
      success: (res) => {
        if (!res.tempFiles || res.tempFiles.length === 0) return;
        this.appendFiles(res.tempFiles);
      }
    });
  },

  // 3. 聊天记录 - 压缩包
  chooseChatArchive() {
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      success: (res) => {
        if (!res.tempFiles || res.tempFiles.length === 0) return;
        const file = res.tempFiles[0];
        const ext = this.getFileExt(file.name);

        if (!ARCHIVE_EXTENSIONS.includes(ext)) {
          wx.showModal({
            title: '格式不正确',
            content: `【${file.name}】非压缩包文件。请选择 .zip 或 .rar 压缩包。`,
            showCancel: false
          });
          return;
        }

        this.inspectArchive(file.path, file.name);
      }
    });
  },

  // --- 手机本地设备选择 ---

  // 4. 本地设备 - 相册多选
  chooseLocalImage() {
    wx.chooseMedia({
      count: 9, // 最多一次选9张照片
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        if (!res.tempFiles || res.tempFiles.length === 0) return;
        const files = res.tempFiles.map((f, idx) => ({
          path: f.tempFilePath,
          name: `照片_${Date.now()}_${idx + 1}.jpg`,
          size: f.size
        }));
        this.appendFiles(files);
      }
    });
  },

  // 5. 本地设备 - 本地文档（支持多选）
  chooseLocalDoc() {
    wx.chooseMessageFile({
      count: 20,
      type: 'file',
      success: (res) => {
        if (!res.tempFiles || res.tempFiles.length === 0) return;
        const validFiles = res.tempFiles.filter(f => DOC_EXTENSIONS.includes(this.getFileExt(f.name)));
        if (validFiles.length === 0) {
          wx.showModal({
            title: '无有效文档',
            content: '所选文件非支持的格式（Word、PDF、Excel、PPT）。',
            showCancel: false
          });
          return;
        }
        this.appendFiles(validFiles);
      }
    });
  },

  // 6. 本地设备 - 本地压缩包
  chooseLocalArchive() {
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      success: (res) => {
        if (!res.tempFiles || res.tempFiles.length === 0) return;
        const file = res.tempFiles[0];
        const ext = this.getFileExt(file.name);

        if (!ARCHIVE_EXTENSIONS.includes(ext)) {
          wx.showModal({
            title: '格式不正确',
            content: `【${file.name}】非压缩包文件。请选择 .zip 或 .rar 压缩包。`,
            showCancel: false
          });
          return;
        }

        this.inspectArchive(file.path, file.name);
      }
    });
  },

  // --- 待打印清单单文件独立参数配置方法 ---

  // 设置单个文件的双面模式
  setFilePrintType(e) {
    const { index, type } = e.currentTarget.dataset;
    const list = this.data.selectedFiles;
    list[index].printType = type;
    this.setData({ selectedFiles: list });
  },

  // 增加单文件份数
  incFileCopies(e) {
    const index = e.currentTarget.dataset.index;
    const list = this.data.selectedFiles;
    if (list[index].copies < 99) {
      list[index].copies += 1;
      this.setData({ selectedFiles: list });
    }
  },

  // 减少单文件份数
  decFileCopies(e) {
    const index = e.currentTarget.dataset.index;
    const list = this.data.selectedFiles;
    if (list[index].copies > 1) {
      list[index].copies -= 1;
      this.setData({ selectedFiles: list });
    }
  },

  // 输入单文件页码范围
  onFilePageRangesInput(e) {
    const index = e.currentTarget.dataset.index;
    const val = e.detail.value.trim();
    const list = this.data.selectedFiles;
    list[index].pageRanges = val;
    this.setData({ selectedFiles: list });
  },

  // 移除单个文件
  removeFile(e) {
    const index = e.currentTarget.dataset.index;
    const list = this.data.selectedFiles;
    list.splice(index, 1);
    this.setData({ selectedFiles: list });
  },

  // 清空所有待打印文件
  clearAllFiles() {
    wx.showModal({
      title: '清空确认',
      content: '确定要清空已选择的所有待打印文档吗？',
      success: (res) => {
        if (res.confirm) {
          this.setData({ selectedFiles: [] });
        }
      }
    });
  },

  // 批量统一设置所有文档的双面模式
  applyBatchPrintType(e) {
    const targetType = e.currentTarget.dataset.type;
    const list = this.data.selectedFiles.map(f => ({
      ...f,
      printType: targetType
    }));
    this.setData({ selectedFiles: list });
    wx.showToast({ title: '已统一设置', icon: 'success' });
  },

  // --- 一键提交打印所有文档（按各自独立参数打印） ---
  async submitAllFilesPrint() {
    const files = this.data.selectedFiles;
    if (!files || files.length === 0) {
      wx.showToast({ title: '请先添加待打印文档', icon: 'none' });
      return;
    }

    const serverUrl = this.data.serverUrl;
    this.setData({ uploading: true });

    let successCount = 0;
    let failCount = 0;

    for (let i = 0; i < files.length; i++) {
      const fileItem = files[i];
      wx.showLoading({ title: `发送 (${i + 1}/${files.length}): ${fileItem.name.substring(0, 8)}...` });

      try {
        await new Promise((resolve, reject) => {
          wx.uploadFile({
            url: `${serverUrl}/api/upload`,
            filePath: fileItem.path,
            name: 'file',
            formData: {
              original_filename: fileItem.name,
              auto_print: 'true',
              print_type: fileItem.printType,
              copies: String(fileItem.copies),
              page_ranges: fileItem.pageRanges
            },
            success: (uploadRes) => {
              try {
                const res = JSON.parse(uploadRes.data);
                if (res.success) {
                  successCount++;
                } else {
                  failCount++;
                }
              } catch (e) {
                failCount++;
              }
              resolve();
            },
            fail: () => {
              failCount++;
              resolve();
            }
          });
        });
      } catch (e) {
        failCount++;
      }
    }

    wx.hideLoading();
    this.setData({ uploading: false });

    let msg = `已成功提交 ${successCount} 个文档！`;
    if (failCount > 0) {
      msg += `（其中 ${failCount} 个失败）`;
    }

    wx.showModal({
      title: '打印任务已提交',
      content: msg,
      showCancel: false,
      success: () => {
        this.setData({ selectedFiles: [] });
        this.fetchHistory();
      }
    });
  },

  // --- 压缩包解压与内容挑选 ---
  inspectArchive(filePath, fileName) {
    const url = this.data.serverUrl;
    wx.showLoading({ title: '正在解压并读取清单...' });

    wx.uploadFile({
      url: `${url}/api/archive/inspect`,
      filePath: filePath,
      name: 'file',
      formData: {
        original_filename: fileName
      },
      success: (uploadRes) => {
        wx.hideLoading();
        try {
          const res = JSON.parse(uploadRes.data);
          if (res.success && res.files && res.files.length > 0) {
            this.setData({
              showArchiveModal: true,
              archiveFiles: res.files,
              selectedArchiveCount: res.files.length,
              isAllArchiveSelected: true
            });
          } else if (res.success && (!res.files || res.files.length === 0)) {
            wx.showModal({
              title: '无可打印内容',
              content: '压缩包已解压，但未检测到 Word、PDF 或图片等可打印文档。',
              showCancel: false
            });
          } else {
            wx.showModal({
              title: '解压失败',
              content: res.message || '压缩包无法解析',
              showCancel: false
            });
          }
        } catch (e) {
          wx.showModal({
            title: '解析异常',
            content: '无法解析服务端数据: ' + uploadRes.data,
            showCancel: false
          });
        }
      },
      fail: (err) => {
        wx.hideLoading();
        wx.showModal({
          title: '传输失败',
          content: '无法连接到打印服务器。' + JSON.stringify(err),
          showCancel: false
        });
      }
    });
  },

  toggleArchiveItem(e) {
    const idx = e.currentTarget.dataset.index;
    const list = this.data.archiveFiles;
    list[idx].selected = !list[idx].selected;

    const count = list.filter(item => item.selected).length;
    this.setData({
      archiveFiles: list,
      selectedArchiveCount: count,
      isAllArchiveSelected: count === list.length
    });
  },

  toggleSelectAllArchive() {
    const targetState = !this.data.isAllArchiveSelected;
    const list = this.data.archiveFiles.map(item => ({
      ...item,
      selected: targetState
    }));
    this.setData({
      archiveFiles: list,
      isAllArchiveSelected: targetState,
      selectedArchiveCount: targetState ? list.length : 0
    });
  },

  onArchivePrintTypeChange(e) {
    this.setData({ archivePrintTypeIndex: e.detail.value });
  },

  incArchiveCopies() {
    if (this.data.archiveCopies < 99) {
      this.setData({ archiveCopies: this.data.archiveCopies + 1 });
    }
  },

  decArchiveCopies() {
    if (this.data.archiveCopies > 1) {
      this.setData({ archiveCopies: this.data.archiveCopies - 1 });
    }
  },

  closeArchiveModal() {
    this.setData({ showArchiveModal: false });
  },

  submitSelectedArchiveFiles() {
    const { serverUrl, archiveFiles, archiveCopies, archivePrintTypeIndex, printTypeValues } = this.data;
    const selectedPaths = archiveFiles.filter(item => item.selected).map(item => item.filepath);

    if (selectedPaths.length === 0) {
      wx.showToast({ title: '请至少勾选一个文件', icon: 'none' });
      return;
    }

    this.setData({ uploading: true });
    wx.showLoading({ title: '正在提交打印任务...' });

    wx.request({
      url: `${serverUrl}/api/print_multiple`,
      method: 'POST',
      data: {
        file_paths: selectedPaths,
        print_type: printTypeValues[archivePrintTypeIndex],
        copies: archiveCopies
      },
      success: (res) => {
        wx.hideLoading();
        this.setData({ uploading: false });
        if (res.statusCode === 200 && res.data && res.data.success) {
          wx.showModal({
            title: '批量打印已提交',
            content: `已成功将选中的 ${selectedPaths.length} 个文档发送至打印队列！`,
            showCancel: false,
            success: () => {
              this.setData({ showArchiveModal: false });
              this.fetchHistory();
            }
          });
        } else {
          wx.showModal({
            title: '提交失败',
            content: (res.data && res.data.message) || '服务器繁忙，请稍后再试',
            showCancel: false
          });
        }
      },
      fail: (err) => {
        wx.hideLoading();
        this.setData({ uploading: false });
        wx.showModal({
          title: '网络请求失败',
          content: '无法连接到打印系统: ' + JSON.stringify(err),
          showCancel: false
        });
      }
    });
  },

  // --- 打印历史：按天分组与折叠展示 ---
  toggleDateGroup(e) {
    const index = e.currentTarget.dataset.index;
    const groups = this.data.dateGroups;
    if (groups && groups[index] !== undefined) {
      const key = `dateGroups[${index}].collapsed`;
      this.setData({
        [key]: !groups[index].collapsed
      });
    }
  },

  groupHistoryByDate(records) {
    if (!records || records.length === 0) return [];

    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    const todayStr = `${y}-${m}-${d}`;

    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    const yy = yesterday.getFullYear();
    const ym = String(yesterday.getMonth() + 1).padStart(2, '0');
    const yd = String(yesterday.getDate()).padStart(2, '0');
    const yesterdayStr = `${yy}-${ym}-${yd}`;

    const groups = [];
    const groupMap = {};

    records.forEach((rec, idx) => {
      let datePart = '更早以前';
      let timePart = '';
      if (rec.print_time) {
        const parts = rec.print_time.replace('T', ' ').split(' ');
        datePart = parts[0] || '更早以前';
        timePart = parts[1] ? parts[1].slice(0, 8) : '';
      }
      rec.displayTime = timePart || rec.print_time || '--:--';
      if (!rec.id) {
        rec.id = 'hist_' + idx;
      }

      if (!groupMap[datePart]) {
        let title = datePart;
        const isToday = (datePart === todayStr);
        if (isToday) {
          title = `今天 (${datePart})`;
        } else if (datePart === yesterdayStr) {
          title = `昨天 (${datePart})`;
        }

        // 今天默认展开(collapsed: false)，其他日期默认折叠(collapsed: true)
        // 若之前已有该分组状态则保留用户手动点击状态
        const existingGroup = (this.data.dateGroups || []).find(g => g.dateKey === datePart);
        const collapsed = existingGroup ? existingGroup.collapsed : !isToday;

        const newGroup = {
          dateKey: datePart,
          title: title,
          isToday: isToday,
          collapsed: collapsed,
          items: []
        };
        groupMap[datePart] = newGroup;
        groups.push(newGroup);
      }

      groupMap[datePart].items.push(rec);
    });

    return groups;
  },

  fetchHistory() {
    const url = this.data.serverUrl;
    if (!url) return;

    wx.request({
      url: `${url}/api/history`,
      method: 'GET',
      success: (res) => {
        if (res.statusCode === 200 && Array.isArray(res.data)) {
          const groups = this.groupHistoryByDate(res.data);
          this.setData({
            dateGroups: groups,
            totalHistoryCount: res.data.length
          });
        }
      },
      fail: (err) => {
        console.error('获取打印历史记录失败:', err);
      }
    });
  }
});
