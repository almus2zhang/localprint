import os
import time
import logging
import threading
import subprocess
from pathlib import Path
from collections import deque
from watchdog.events import FileSystemEventHandler
from database import DatabaseManager
from config import ARCHIVE_EXTENSIONS, CONVERTIBLE_EXTENSIONS

class FileHandler(FileSystemEventHandler):
    def __init__(self, watch_directory, extract_to=None, db_manager: DatabaseManager = None, conversion_service = None, socketio = None):
        self.watch_directory = watch_directory
        self.autofile_dir = os.path.join(watch_directory, "autofile")
        self.extract_to = extract_to or os.path.join(watch_directory, "extracted")
        self.convert_to = os.path.join(watch_directory, "converted_pdfs")
        self.db_manager = db_manager
        self.conversion_service = conversion_service
        self.socketio = socketio
        self.processed_files = set()
        self.auto_print_files = set()  # 需要自动打印的文件（位于 autofile 目录）
        self._process_lock = threading.Lock()  # 文件处理互斥锁，防止重复处理/打印
        self.file_queue = deque()
        self.processing = False
        self.extracted_files_queue = deque()
        self.conversion_queue = deque()
        
        os.makedirs(self.autofile_dir, exist_ok=True)
        os.makedirs(self.extract_to, exist_ok=True)
        os.makedirs(self.convert_to, exist_ok=True)
        
        self.process_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.process_thread.start()
        
        self.extracted_process_thread = threading.Thread(target=self._process_extracted_queue, daemon=True)
        self.extracted_process_thread.start()
        
        self.conversion_thread = threading.Thread(target=self._process_conversion_queue, daemon=True)
        self.conversion_thread.start()
        
        self.start_time = time.time()
        
        logging.info(f"文件监控已启动: {watch_directory}")
        logging.info(f"自动打印目录: {self.autofile_dir} (长边翻页双面打印)")

    def on_created(self, event):
        if not event.is_directory:
            file_path = event.src_path
            file_creation_time = self._get_file_creation_time(file_path)
            if file_creation_time >= self.start_time:
                logging.info(f"检测到新文件: {os.path.basename(file_path)}")
                self._add_to_queue(file_path)

    def on_moved(self, event):
        if not event.is_directory:
            file_path = event.dest_path
            file_creation_time = self._get_file_creation_time(file_path)
            if file_creation_time >= self.start_time:
                logging.info(f"检测到移动文件: {os.path.basename(file_path)}")
                self._add_to_queue(file_path)

    def _get_file_creation_time(self, file_path):
        try:
            return os.path.getctime(file_path)
        except OSError:
            return os.path.getmtime(file_path)

    def _is_autofile(self, file_path):
        """判断文件是否位于 autofile 自动打印目录内"""
        try:
            autofile = os.path.abspath(self.autofile_dir)
            path = os.path.abspath(file_path)
            return path == autofile or path.startswith(autofile + os.sep)
        except Exception:
            return False

    def _add_to_queue(self, file_path):
        if file_path not in self.processed_files:
            if self._is_autofile(file_path):
                self.auto_print_files.add(file_path)
                logging.info(f"检测到自动打印目录文件: {os.path.basename(file_path)}")
            self.file_queue.append(file_path)

    def _add_extracted_to_queue(self, file_path, auto_print=False):
        if file_path not in self.processed_files:
            if auto_print:
                self.auto_print_files.add(file_path)
            self.extracted_files_queue.append(file_path)
    
    def _add_to_conversion_queue(self, file_path):
        """添加到转换队列"""
        if file_path not in self.processed_files:
            self.conversion_queue.append(file_path)

    def _process_queue(self):
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
            time.sleep(0.5)

    def _process_extracted_queue(self):
        while True:
            if self.extracted_files_queue:
                file_path = self.extracted_files_queue.popleft()
                try:
                    if self._wait_for_file_stable(file_path):
                        self._process_single_file(file_path)
                except Exception as e:
                    logging.error(f"处理解压文件 {file_path} 时出错: {str(e)}")
            time.sleep(0.5)
    
    def _process_conversion_queue(self):
        """处理文档转换队列"""
        while True:
            if self.conversion_queue:
                file_path = self.conversion_queue.popleft()
                try:
                    if self._wait_for_file_stable(file_path):
                        logging.info(f"开始转换文档: {os.path.basename(file_path)}")
                        if self.conversion_service:
                            pdf_path = self.conversion_service.convert_to_pdf(file_path)
                            if pdf_path and os.path.exists(pdf_path):
                                # 记录文件处理历史
                                if self.db_manager:
                                    self.db_manager.add_file_record(
                                        filename=os.path.basename(file_path),
                                        filepath=file_path,
                                        file_type='converted'
                                    )
                                # 等待PDF文件稳定
                                time.sleep(2)
                                logging.info(f"转换后的PDF已准备好: {os.path.basename(pdf_path)}")
                                if self.socketio:
                                    self.socketio.emit('conversion_complete', {'filename': os.path.basename(pdf_path), 'original': os.path.basename(file_path)})
                                    self.socketio.emit('file_updated', {'type': 'new_pdf', 'filename': os.path.basename(pdf_path)})

                                # autofile 目录的转换结果自动打印（长边翻页双面打印）
                                if file_path in self.auto_print_files:
                                    logging.info(f"自动打印转换后的PDF: {os.path.basename(pdf_path)}")
                                    if self._print_file(pdf_path, print_type='two-sided-long-edge'):
                                        self.processed_files.add(pdf_path)
                                        logging.info(f"自动打印任务已发送: {os.path.basename(pdf_path)}")
                                    else:
                                        logging.error(f"自动打印失败: {os.path.basename(pdf_path)}")
                        else:
                            logging.error("ConversionService未初始化")
                except Exception as e:
                    logging.error(f"处理转换文件 {file_path} 时出错: {str(e)}")
            time.sleep(0.5)

    def _process_file(self, file_path):
        if not self._wait_for_file_stable(file_path):
            return
        
        try:
            self._process_single_file(file_path)
        except Exception as e:
            logging.error(f"处理文件 {file_path} 时出错: {str(e)}")
    
    def _process_single_file(self, file_path):
        """处理单个文件的核心逻辑（加锁串行化，避免同一文件并发处理）"""
        with self._process_lock:
            return self._process_single_file_inner(file_path)

    def _process_single_file_inner(self, file_path):
        """处理单个文件的核心逻辑"""
        # 忽略转换目录中的文件
        if self.convert_to in file_path:
            logging.info(f"忽略转换目录中的文件: {file_path}")
            return
        # 去重：同一文件可能同时进入解压队列和主队列（watchdog事件），只处理一次
        if file_path in self.processed_files:
            logging.debug(f"文件已处理过，跳过: {os.path.basename(file_path)}")
            return
        file_ext = Path(file_path).suffix.lower()
        auto_print = file_path in self.auto_print_files
        
        if self._is_archive_file(file_path):
            logging.info(f"开始解压文件: {Path(file_path).name}")
            # 注意: 这里原来调用 self._extract_file，现在该方法在 ConversionService 中
            # 但 ConversionService 设计为处理文档转换。
            # 实际上解压逻辑可以放在FileHandler中，或者ConversionService中。
            # 为了简单，我将_extract_file逻辑复制到这里或者从Service调用。
            # 鉴于解压更像一种变换，我们可以让ConversionService处理，或者直接在这里实现。
            # 之前的ConversionService包含了_extract_file，所以我们调用它。
            # 但是为了避免 FileHandler 太重，且解压需要把解压出的文件再加回队列，
            # 最好是 ConversionService 提供单纯的解压功能。
            
            # 由于我在ConversionService中没有暴露 extract_file，这里需要修复一下。
            # 实际上在plan中提到 "Move _convert_to_pdf logic here"，解压逻辑也是一种处理。
            # 让我们假设 ConversionService 也包含了 extract_file 方法。
            # 但我在上一步写入 conversion.py 时，包含了 _extract_file 以及 _extract_zip_with_encoding 等。
            # 不过我把它们设为了私有方法 (_开头)。
            # 我们应该在 ConversionService 中暴露一个公开方法 extract_archive。
            
            # 临时方案：如果 ConversionService 有 extract_archive，我们调用它。
            # 实际上我在上一步只写了 convert_to_pdf，但是包含了 _extract_file 的代码块（我检查一下上一步的输出...）
            # 上一步的 conversion.py 内容包含了 _extract_zip_with_encoding 等方法，
            # 同时也包含了 _extract_file。
            # 所以我们需要把 ConversionService 传递进来，并调用它的内部方法（虽然不推荐）或者修改 ConversionService 暴露接口。
            # 为了稳妥，我这里先假设 ConversionService 实例有 _extract_file (Python没有真正的私有)。
            # 并且 ConversionService 的 _extract_file 里面用到了 self.watch_directory 等属性，这可能是个问题。
            # 原来的类不仅包含转换还包含解压。
            # 让我们检查一下 conversion.py 的内容。
            # conversion.py 的 _extract_file 用到了 self.watch_directory。
            # 但是 conversion.py 的 __init__ 只接受 convert_dir 和 db_manager。
            # 所以 conversion.py 的代码在 _extract_file 处会报错。
            
            # **修正策略**: 
            # 鉴于 FileHandler 更适合处理文件流转，而 ConversionService 适合处理单一文件的变换。
            # 解压后的文件需要重新入队，这需要访问 FileHandler 的队列。
            # 最好的办法是：把解压逻辑留在 FileHandler，或者 FileHandler 传一个 callback 给 ConversionService。
            # 考虑到时间，我把解压逻辑保留在 FileHandler 中（需要复制相关辅助函数），
            # 或者修改 ConversionService 使其能工作。
            
            # 让我们在 FileHandler 中实现解压逻辑，因为它需要操作 self.extracted_files_queue。
            # 这意味着我需要把 _extract_zip_with_encoding 等方法复制过来，或者从 conversion.py 导入 EncodingHandler。
            
            if self._extract_archive(file_path, auto_print=auto_print):
                 if self.db_manager:
                    self.db_manager.add_file_record(
                        filename=os.path.basename(file_path),
                        filepath=file_path,
                        file_type='extracted'
                    )
                 self.processed_files.add(file_path)
                 logging.info(f"文件解压完成: {Path(file_path).name}")
                
        elif self._is_printable_file(file_path):
            # PDF文件无需转换
            if auto_print:
                logging.info(f"检测到PDF文件，开始自动打印: {Path(file_path).name}")
                if self._print_file(file_path, print_type='two-sided-long-edge'):
                    self.processed_files.add(file_path)
                    logging.info(f"自动打印任务已发送: {Path(file_path).name}")
                else:
                    logging.error(f"自动打印失败: {Path(file_path).name}")
            else:
                logging.info(f"检测到PDF文件，跳过转换和打印: {Path(file_path).name}")
                if self.db_manager:
                    self.db_manager.add_file_record(
                        filename=os.path.basename(file_path),
                        filepath=file_path,
                        file_type='detected_pdf'
                    )
                self.processed_files.add(file_path)
            if self.socketio:
                self.socketio.emit('file_updated', {'type': 'new_pdf', 'filename': os.path.basename(file_path)})
                
        elif self._is_convertible_file(file_path):
            # 添加到转换队列
            if auto_print:
                logging.info(f"添加到转换队列（自动打印）: {Path(file_path).name}")
            else:
                logging.info(f"添加到转换队列: {Path(file_path).name}")
            self._add_to_conversion_queue(file_path)
            self.processed_files.add(file_path)

    def _wait_for_file_stable(self, file_path, max_attempts=30, wait_interval=1):
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
        return False

    def _is_archive_file(self, file_path):
        file_ext = Path(file_path).suffix.lower()
        if file_ext in ARCHIVE_EXTENSIONS:
            return True
        if file_path.endswith('.tar.gz') or file_path.endswith('.tar.bz2'):
            return True
        return False

    def _is_printable_file(self, file_path):
        """检查是否为可直接打印的文件（仅PDF）"""
        return Path(file_path).suffix.lower() == '.pdf'
    
    def _is_convertible_file(self, file_path):
        """检查是否为需要转换的文件（文档和图片）"""
        file_ext = Path(file_path).suffix.lower()
        return file_ext in CONVERTIBLE_EXTENSIONS or file_ext in {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}

    def _get_document_page_count(self, file_path):
        """计算文档页数（支持 PDF 与图片）"""
        try:
            ext = Path(file_path).suffix.lower()
            if ext in {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp'}:
                return 1
            if ext == '.pdf' and os.path.exists(file_path):
                try:
                    import pypdf
                    reader = pypdf.PdfReader(file_path)
                    return len(reader.pages)
                except Exception:
                    pass
                try:
                    import re
                    with open(file_path, 'rb') as f:
                        content = f.read()
                        matches = re.findall(rb"/Type\s*/Page[^s]", content)
                        if matches:
                            return len(matches)
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"获取文档页数异常: {e}")
        return 1

    def _print_file(self, file_path, print_type='two-sided-short-edge', page_ranges='', copies=1):
        """
        打印文件并记录文档名与页数
        """
        import re
        from services.print_manager import PrintService

        try:
            file_ext = Path(file_path).suffix.lower()
            # 提取干净的文档名（去除时间戳前缀）
            raw_filename = Path(file_path).name
            clean_filename = re.sub(r'^\d{8}[_]?\d{4,6}_', '', raw_filename)
            pages = self._get_document_page_count(file_path)

            cmd = ['lp']
            # 指定作业标题为真实文档名
            cmd.extend(['-t', clean_filename])

            if print_type == 'one-sided':
                cmd.extend(['-o', 'sides=one-sided'])
            elif print_type == 'two-sided-long-edge':
                cmd.extend(['-o', 'sides=two-sided-long-edge'])
            else:
                cmd.extend(['-o', 'sides=two-sided-short-edge'])
            
            if file_ext in {'.jpg', '.jpeg', '.png'}:
                cmd.extend(['-o', 'fit-to-page'])
            
            if page_ranges:
                cmd.extend(['-P', str(page_ranges)])

            try:
                c = int(copies)
                if c > 1:
                    cmd.extend(['-n', str(c)])
            except (ValueError, TypeError):
                pass
            
            cmd.append(file_path)
            
            logging.info(f"执行打印命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            if result.returncode == 0:
                job_id = None
                if 'request id is' in result.stdout:
                    job_id = result.stdout.split('request id is ')[1].split()[0]
                
                # 注册到 PrintService 缓存中，供队列查询
                if job_id:
                    PrintService.register_job(job_id, filename=clean_filename, pages=pages, filepath=file_path, print_type=print_type)

                if self.db_manager:
                    self.db_manager.add_print_record(
                        filename=clean_filename,
                        filepath=file_path,
                        pages=pages,
                        print_type=print_type,
                        status='success',
                        print_command=' '.join(cmd)
                    )
                return True
            else:
                if self.db_manager:
                    self.db_manager.add_print_record(
                        filename=clean_filename,
                        filepath=file_path,
                        pages=pages,
                        print_type=print_type,
                        status='failed',
                        print_command=' '.join(cmd),
                        error_message=result.stderr
                    )
                return False
        except Exception as e:
            logging.error(f"打印失败: {e}")
            if self.db_manager:
                self.db_manager.add_print_record(
                        filename=clean_filename if 'clean_filename' in locals() else Path(file_path).name,
                        filepath=file_path,
                        pages=pages if 'pages' in locals() else 0,
                        print_type=print_type,
                        status='failed',
                        error_message=str(e)
                    )
            return False

    # 这里为了简化，我需要引入解压逻辑使用的 EncodingHandler
    # 我们可以从 services.conversion 导入
    
    def _extract_archive(self, file_path, auto_print=False):
        import zipfile
        import tarfile
        import shutil
        from services.conversion import EncodingHandler # 假设在同一包下
        
        try:
            file_ext = Path(file_path).suffix.lower()
            relative_path = os.path.relpath(file_path, self.watch_directory)
            safe_dir_name = relative_path.replace(os.path.sep, '_')
            extract_dir = os.path.join(self.extract_to, safe_dir_name)
            os.makedirs(extract_dir, exist_ok=True)
            
            if file_ext == '.zip' or file_path.endswith('.zip'):
                return self._extract_zip(file_path, extract_dir, auto_print)
            elif file_ext == '.tar' or file_path.endswith('.tar.gz') or file_path.endswith('.tar.bz2'):
                mode = 'r'
                if file_path.endswith('.gz') or file_path.endswith('.tar.gz'):
                    mode = 'r:gz'
                elif file_path.endswith('.bz2') or file_path.endswith('.tar.bz2'):
                    mode = 'r:bz2'
                return self._extract_tar(file_path, extract_dir, mode, auto_print)
            else:
                return False
        except Exception as e:
            logging.error(f"解压文件 {file_path} 时出错: {str(e)}")
            return False

    def _extract_zip(self, file_path, extract_dir, auto_print=False):
        import zipfile
        import shutil
        from services.conversion import EncodingHandler
        try:
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                for file_info in zip_ref.filelist:
                    try:
                        # 彻底消除中文乱码：自动识别 UTF-8 / GB18030 / GBK / Big5
                        decoded_name = EncodingHandler.decode_zip_filename(file_info)
                        safe_rel = EncodingHandler.safe_relpath(decoded_name)
                        if not safe_rel:
                            continue

                        full_path = os.path.join(extract_dir, safe_rel)

                        if file_info.is_dir():
                            os.makedirs(full_path, exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(full_path), exist_ok=True)
                            with zip_ref.open(file_info) as source, open(full_path, 'wb') as target:
                                shutil.copyfileobj(source, target)
                            if auto_print:
                                self._add_extracted_to_queue(full_path, auto_print)
                    except Exception as e:
                        logging.error(f"解压ZIP项失败 [{getattr(file_info, 'filename', '')}]: {e}")
            return True
        except Exception as e:
            logging.error(f"解压ZIP失败 [{file_path}]: {e}")
            return False

    def _extract_tar(self, file_path, extract_dir, mode='r', auto_print=False):
        import tarfile
        import shutil
        from services.conversion import EncodingHandler
        try:
            with tarfile.open(file_path, mode, errors='surrogateescape') as tar_ref:
                for member in tar_ref:
                    try:
                        decoded_name = EncodingHandler.decode_tar_member(member)
                        safe_rel = EncodingHandler.safe_relpath(decoded_name)
                        if not safe_rel:
                            continue

                        full_path = os.path.join(extract_dir, safe_rel)

                        if member.isdir():
                            os.makedirs(full_path, exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(full_path), exist_ok=True)
                            f_in = tar_ref.extractfile(member)
                            if f_in:
                                with open(full_path, 'wb') as f_out:
                                    shutil.copyfileobj(f_in, f_out)
                                if auto_print:
                                    self._add_extracted_to_queue(full_path, auto_print)
                    except Exception as e:
                        logging.error(f"解压TAR项失败 [{member.name}]: {e}")
            return True
        except Exception as e:
            logging.error(f"解压TAR失败 [{file_path}]: {e}")
            return False
