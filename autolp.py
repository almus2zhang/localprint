#!/usr/bin/env python3
"""
文件监控与处理程序
功能：监控指定目录及其子目录，只处理新增文件（压缩包解压，文档打印）
默认使用短边翻页双面打印
增强文件名编码处理，解决乱码问题
修复文件稳定性检测错误
修复解压后文件不打印的问题
支持子目录监控
修复文件名编码问题
"""

import os
import time
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import zipfile
import tarfile
import subprocess
from pathlib import Path
import threading
from collections import deque
import chardet
import shutil
import sys

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/file_monitor.log'),
        logging.StreamHandler()
    ]
)

class EncodingHandler:
    """文件名编码处理类"""
    
    # 常见编码列表，按优先级排序
    ENCODINGS = ['utf-8', 'gbk', 'gb2312', 'big5', 'shift_jis', 'euc-kr', 'iso-8859-1', 'cp437']
    
    @staticmethod
    def detect_encoding(data):
        """检测数据编码"""
        try:
            result = chardet.detect(data)
            encoding = result['encoding']
            confidence = result['confidence']
            
            if encoding and confidence > 0.6:
                return encoding
        except:
            pass
        
        return None
    
    @staticmethod
    def decode_filename(filename_bytes, archive_encoding=None):
        """解码文件名，尝试多种编码"""
        # 如果已知压缩包编码，直接使用
        if archive_encoding:
            try:
                return filename_bytes.decode(archive_encoding)
            except (UnicodeDecodeError, LookupError):
                pass
        
        # 尝试常见编码
        for encoding in EncodingHandler.ENCODINGS:
            try:
                return filename_bytes.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        
        # 如果所有编码都失败，使用替代方案
        try:
            # 尝试使用系统默认编码
            return filename_bytes.decode('utf-8', errors='replace')
        except:
            # 最后手段：使用ASCII并替换无法解码的字符
            return filename_bytes.decode('ascii', errors='replace')
    
    @staticmethod
    def safe_filename(filename):
        """创建安全的文件名，移除或替换不安全的字符"""
        # 替换Windows不安全的字符
        unsafe_chars = ['<', '>', ':', '"', '|', '?', '*', '\\', '/']
        safe_name = filename
        for char in unsafe_chars:
            safe_name = safe_name.replace(char, '_')
        
        # 移除控制字符
        safe_name = ''.join(char for char in safe_name if ord(char) >= 32)
        
        # 限制文件名长度
        if len(safe_name) > 255:
            name, ext = os.path.splitext(safe_name)
            safe_name = name[:255-len(ext)] + ext
        
        return safe_name

class FileHandler(FileSystemEventHandler):
    def __init__(self, watch_directory, extract_to=None):
        self.watch_directory = watch_directory
        self.extract_to = extract_to or os.path.join(watch_directory, "extracted")
        self.processed_files = set()  # 记录已处理文件
        self.file_queue = deque()     # 文件处理队列
        self.processing = False       # 是否正在处理
        self.extracted_files_queue = deque()  # 解压文件队列
        
        # 创建解压目录
        os.makedirs(self.extract_to, exist_ok=True)
        
        # 启动文件处理线程
        self.process_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.process_thread.start()
        
        # 启动解压文件处理线程
        self.extracted_process_thread = threading.Thread(target=self._process_extracted_queue, daemon=True)
        self.extracted_process_thread.start()
        
        # 记录程序启动时间
        self.start_time = time.time()
        
        logging.info(f"文件监控已启动，开始时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.start_time))}")
        logging.info(f"监控目录: {watch_directory} (包含所有子目录)")
        logging.info(f"解压目录: {self.extract_to}")
        logging.info("默认打印设置: 双面打印(短边翻页)")

    def on_created(self, event):
        """当有新文件创建时调用 - 只处理程序启动后的新文件"""
        if not event.is_directory:
            file_path = event.src_path
            file_creation_time = self._get_file_creation_time(file_path)
            
            # 只处理程序启动后创建的文件
            if file_creation_time >= self.start_time:
                logging.info(f"检测到新文件: {os.path.basename(file_path)} (位置: {os.path.dirname(file_path)})")
                self._add_to_queue(file_path)
            else:
                logging.debug(f"忽略程序启动前已存在的文件: {os.path.basename(file_path)}")

    def on_moved(self, event):
        """当文件被移动时调用 - 只处理程序启动后的新文件"""
        if not event.is_directory:
            file_path = event.dest_path
            file_creation_time = self._get_file_creation_time(file_path)
            
            # 只处理程序启动后创建的文件
            if file_creation_time >= self.start_time:
                logging.info(f"检测到移动文件: {os.path.basename(file_path)} (位置: {os.path.dirname(file_path)})")
                self._add_to_queue(file_path)
            else:
                logging.debug(f"忽略程序启动前已存在的移动文件: {os.path.basename(file_path)}")

    def _get_file_creation_time(self, file_path):
        """获取文件创建时间"""
        try:
            return os.path.getctime(file_path)
        except OSError:
            # 如果无法获取创建时间，使用修改时间
            return os.path.getmtime(file_path)

    def _add_to_queue(self, file_path):
        """添加文件到处理队列"""
        if file_path not in self.processed_files:
            self.file_queue.append(file_path)
            logging.debug(f"文件已添加到队列: {os.path.basename(file_path)}")

    def _add_extracted_to_queue(self, file_path):
        """添加解压后的文件到处理队列"""
        if file_path not in self.processed_files:
            self.extracted_files_queue.append(file_path)
            logging.debug(f"解压文件已添加到队列: {os.path.basename(file_path)}")

    def _process_queue(self):
        """处理文件队列的线程函数"""
        while True:
            if self.file_queue and not self.processing:
                file_path = self.file_queue.popleft()
                self.processing = True
                try:
                    self._process_file(file_path)
                except Exception as e:
                    logging.error(f"处理文件 {file_path} 时出错: {str(e)}")
                finally:
                    self.processing = False
            time.sleep(0.5)  # 降低CPU使用率

    def _process_extracted_queue(self):
        """处理解压文件队列的线程函数"""
        while True:
            if self.extracted_files_queue:
                file_path = self.extracted_files_queue.popleft()
                try:
                    # 等待文件完全写入
                    if self._wait_for_file_stable(file_path):
                        if self._is_printable_file(file_path):
                            logging.info(f"开始打印解压文件: {os.path.basename(file_path)}")
                            if self._print_file(file_path):
                                self.processed_files.add(file_path)
                                logging.info(f"解压文件打印任务已发送: {os.path.basename(file_path)}")
                            else:
                                logging.error(f"解压文件打印失败: {os.path.basename(file_path)}")
                        else:
                            logging.debug(f"解压文件不可打印，跳过: {os.path.basename(file_path)}")
                    else:
                        logging.warning(f"解压文件未稳定，跳过处理: {os.path.basename(file_path)}")
                except Exception as e:
                    logging.error(f"处理解压文件 {file_path} 时出错: {str(e)}")
            time.sleep(0.5)  # 降低CPU使用率

    def _process_file(self, file_path):
        """处理单个文件"""
        # 等待文件完全写入
        if not self._wait_for_file_stable(file_path):
            logging.warning(f"文件未稳定，跳过处理: {os.path.basename(file_path)}")
            return
        
        try:
            file_ext = Path(file_path).suffix.lower()
            file_name = Path(file_path).name
            
            # 处理压缩文件
            if self._is_archive_file(file_path):
                logging.info(f"开始解压文件: {file_name} (位置: {os.path.dirname(file_path)})")
                if self._extract_file(file_path):
                    self.processed_files.add(file_path)
                    logging.info(f"文件解压完成: {file_name}")
                else:
                    logging.error(f"文件解压失败: {file_name}")
                
            # 处理可打印文件
            elif self._is_printable_file(file_path):
                logging.info(f"开始打印文件: {file_name} (位置: {os.path.dirname(file_path)})")
                if self._print_file(file_path):
                    self.processed_files.add(file_path)
                    logging.info(f"文件打印任务已发送: {file_name}")
                else:
                    logging.error(f"文件打印失败: {file_name}")
                    
        except Exception as e:
            logging.error(f"处理文件 {file_path} 时出错: {str(e)}")

    def _wait_for_file_stable(self, file_path, max_attempts=30, wait_interval=1):
        """等待文件稳定（停止增长）"""
        file_size = -1
        for attempt in range(max_attempts):
            try:
                current_size = os.path.getsize(file_path)
                if current_size == file_size and current_size > 0:
                    return True
                file_size = current_size
                time.sleep(wait_interval)
            except OSError:
                time.sleep(wait_interval)
        
        logging.warning(f"文件在 {max_attempts * wait_interval} 秒后仍未稳定: {os.path.basename(file_path)}")
        return False

    def _is_archive_file(self, file_path):
        """检查是否为压缩文件"""
        archive_extensions = {'.zip', '.tar', '.gz', '.bz2', '.7z', '.rar'}
        file_ext = Path(file_path).suffix.lower()
        
        # 检查常见压缩格式
        if file_ext in archive_extensions:
            return True
            
        # 检查tar.gz等复合扩展名
        if file_path.endswith('.tar.gz') or file_path.endswith('.tar.bz2'):
            return True
            
        return False

    def _is_printable_file(self, file_path):
        """检查是否为可打印文件"""
        printable_extensions = {'.doc', '.docx', '.pdf', '.txt', '.jpg', '.jpeg', '.png'}
        file_ext = Path(file_path).suffix.lower()
        return file_ext in printable_extensions

    def _extract_zip_with_encoding(self, file_path, extract_dir):
        """解压ZIP文件，处理文件名编码问题"""
        try:
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                # 获取文件名列表，尝试检测编码
                file_list = zip_ref.namelist()
                
                # 尝试检测压缩包的文件名编码
                encoding = None
                if file_list:
                    # 使用第一个文件名来检测编码
                    try:
                        sample_name = file_list[0].encode('cp437')  # ZIP文件通常使用CP437
                        detected = EncodingHandler.detect_encoding(sample_name)
                        if detected:
                            encoding = detected
                            logging.info(f"检测到ZIP文件编码: {encoding}")
                    except Exception as e:
                        logging.warning(f"检测ZIP文件编码时出错: {str(e)}")
                
                # 逐个解压文件，处理文件名
                for file_info in zip_ref.filelist:
                    try:
                        # 获取原始文件名字节
                        original_filename = file_info.filename
                        
                        # 解码文件名
                        try:
                            # 先尝试使用UTF-8（ZIP规范推荐）
                            decoded_name = original_filename.encode('cp437').decode('utf-8')
                        except (UnicodeDecodeError, UnicodeEncodeError):
                            try:
                                # 尝试使用检测到的编码
                                if encoding:
                                    decoded_name = original_filename.encode('cp437').decode(encoding)
                                else:
                                    # 最后尝试GBK（常见中文编码）
                                    decoded_name = original_filename.encode('cp437').decode('gbk')
                            except (UnicodeDecodeError, UnicodeEncodeError):
                                # 如果所有编码都失败，使用原始名称
                                decoded_name = original_filename
                                logging.warning(f"无法解码文件名: {original_filename}, 使用原始名称")
                        
                        # 创建安全的文件名
                        safe_name = EncodingHandler.safe_filename(decoded_name)
                        
                        # 构建完整路径
                        full_path = os.path.join(extract_dir, safe_name)
                        
                        # 创建目录（如果需要）
                        os.makedirs(os.path.dirname(full_path), exist_ok=True)
                        
                        # 解压文件
                        if not file_info.is_dir():
                            with zip_ref.open(file_info) as source, open(full_path, 'wb') as target:
                                shutil.copyfileobj(source, target)
                                
                            # 如果是可打印文件，添加到解压文件队列
                            if self._is_printable_file(full_path):
                                self._add_extracted_to_queue(full_path)
                    except Exception as e:
                        logging.error(f"解压ZIP中的文件 {file_info.filename} 时出错: {str(e)}")
                        continue  # 继续处理下一个文件
            
            return True
            
        except Exception as e:
            logging.error(f"解压ZIP文件 {file_path} 时出错: {str(e)}")
            return False

    def _extract_tar_with_encoding(self, file_path, extract_dir, mode='r'):
        """解压TAR文件，处理文件名编码问题"""
        try:
            with tarfile.open(file_path, mode) as tar_ref:
                # 获取成员列表
                members = tar_ref.getmembers()
                
                # 尝试检测编码
                encoding = None
                if members:
                    # 使用第一个成员名来检测编码
                    try:
                        sample_name = members[0].name
                        detected = EncodingHandler.detect_encoding(sample_name.encode('utf-8', errors='replace'))
                        if detected:
                            encoding = detected
                            logging.info(f"检测到TAR文件编码: {encoding}")
                    except Exception as e:
                        logging.warning(f"检测TAR文件编码时出错: {str(e)}")
                
                # 逐个解压文件，处理文件名
                for member in members:
                    try:
                        # 解码文件名
                        try:
                            if encoding:
                                decoded_name = member.name.encode('utf-8').decode(encoding)
                            else:
                                # 尝试常见编码
                                decoded_name = EncodingHandler.decode_filename(member.name.encode('utf-8'))
                        except:
                            decoded_name = member.name
                            logging.warning(f"无法解码文件名: {member.name}, 使用原始名称")
                        
                        # 创建安全的文件名
                        safe_name = EncodingHandler.safe_filename(decoded_name)
                        
                        # 设置解码后的名称
                        member.name = safe_name
                        
                        # 解压文件
                        tar_ref.extract(member, extract_dir)
                        
                        # 如果是可打印文件，添加到解压文件队列
                        if not member.isdir() and self._is_printable_file(member.name):
                            full_path = os.path.join(extract_dir, member.name)
                            self._add_extracted_to_queue(full_path)
                    except Exception as e:
                        logging.error(f"解压TAR中的文件 {member.name} 时出错: {str(e)}")
                        continue  # 继续处理下一个文件
            
            return True
            
        except Exception as e:
            logging.error(f"解压TAR文件 {file_path} 时出错: {str(e)}")
            return False

    def _extract_file(self, file_path):
        """解压文件，处理文件名编码问题"""
        try:
            file_ext = Path(file_path).suffix.lower()
            file_name = Path(file_path).stem
            
            # 创建解压子目录 - 包含相对路径信息以避免冲突
            relative_path = os.path.relpath(file_path, self.watch_directory)
            safe_dir_name = relative_path.replace(os.path.sep, '_')
            extract_dir = os.path.join(self.extract_to, safe_dir_name)
            os.makedirs(extract_dir, exist_ok=True)
            
            # 根据文件类型选择解压方法
            if file_ext == '.zip' or file_path.endswith('.zip'):
                return self._extract_zip_with_encoding(file_path, extract_dir)
                    
            elif file_ext == '.tar' or file_path.endswith('.tar.gz') or file_path.endswith('.tar.bz2'):
                mode = 'r'
                if file_path.endswith('.gz') or file_path.endswith('.tar.gz'):
                    mode = 'r:gz'
                elif file_path.endswith('.bz2') or file_path.endswith('.tar.bz2'):
                    mode = 'r:bz2'
                    
                return self._extract_tar_with_encoding(file_path, extract_dir, mode)
                    
            else:
                logging.warning(f"不支持的压缩格式: {file_ext}")
                return False
                
        except Exception as e:
            logging.error(f"解压文件 {file_path} 时出错: {str(e)}")
            return False

    def _print_file(self, file_path):
        """打印文件 - 默认使用短边翻页双面打印"""
        try:
            file_ext = Path(file_path).suffix.lower()
            file_name = Path(file_path).name
            
            # 基本打印命令
            cmd = ['llp']
            
            # 添加双面打印选项 - 短边翻页
            cmd.extend(['-o', 'sides=two-sided-short-edge'])
            
            if file_ext in {'.jpg', '.jpeg', '.png'}:
                # 图片适应页面
                cmd.extend(['-o', 'fit-to-page'])
            
            # 添加文件路径
            cmd.append(file_path)
            
            logging.info(f"执行打印命令: {' '.join(cmd)}")
            
            # 执行打印
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            if result.returncode == 0:
                # 解析打印作业ID
                if 'request id is' in result.stdout:
                    job_id = result.stdout.split('request id is ')[1].split()[0]
                    logging.info(f"打印作业已创建: {job_id}")
                return True
            else:
                logging.error(f"打印命令返回错误: {result.stderr}")
                return False
                
        except subprocess.CalledProcessError as e:
            logging.error(f"打印文件 {file_path} 时出错: {str(e)}")
            if e.stderr:
                logging.error(f"错误详情: {e.stderr}")
            return False
        except Exception as e:
            logging.error(f"打印过程中发生错误: {str(e)}")
            return False

def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='文件监控与处理程序 - 只处理新增文件')
    parser.add_argument('directory', help='要监控的目录路径')
    parser.add_argument('--extract-to', help='解压文件的目标目录', default=None)
    
    args = parser.parse_args()
    
    watch_directory = os.path.abspath(args.directory)
    
    if not os.path.exists(watch_directory):
        logging.error(f"监控目录不存在: {watch_directory}")
        return
    
    logging.info(f"开始监控目录: {watch_directory} (包含所有子目录)")
    logging.info("只处理程序启动后的新增文件")
    logging.info("默认打印设置: 双面打印(短边翻页)")
    logging.info("文件名编码处理: 已启用")
    logging.info("解压文件自动打印: 已启用")
    
    event_handler = FileHandler(watch_directory, args.extract_to)
    observer = Observer()
    
    # 启用递归监控，监控所有子目录
    observer.schedule(event_handler, watch_directory, recursive=True)
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        logging.info("监控程序已停止")
    
    observer.join()

if __name__ == "__main__":
    # 设置默认编码为UTF-8
    if sys.version_info >= (3, 7):
        os.environ["PYTHONUTF8"] = "1"
    
    # 检查必要的系统工具
    #try:
    #    subprocess.run(['lp', '--version'], capture_output=True, check=True)
    #except (subprocess.CalledProcessError, FileNotFoundError):
    #    logging.error("CUPS打印系统未安装或不可用，请先安装: sudo apt install cups cups-client")
    #    exit(1)
    
    # 检查chardet库
    try:
        import chardet
    except ImportError:
        logging.error("chardet库未安装，请先安装: pip3 install chardet")
        exit(1)
    
    main()