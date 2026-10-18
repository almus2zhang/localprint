import os
import time
import re
import unicodedata
import logging
import subprocess
from pathlib import Path
from database import DatabaseManager

try:
    import chardet
except ImportError:
    chardet = None

class EncodingHandler:
    """文件名编码处理类，彻底解决中文乱码问题"""
    
    ENCODINGS = ['utf-8', 'gb18030', 'gbk', 'gb2312', 'big5', 'shift_jis', 'euc-kr', 'cp437', 'iso-8859-1']
    
    @staticmethod
    def detect_encoding(data):
        """检测数据编码（支持 chardet 容错）"""
        if chardet is not None:
            try:
                result = chardet.detect(data)
                encoding = result.get('encoding')
                confidence = result.get('confidence', 0)
                if encoding and confidence > 0.6:
                    return encoding
            except Exception:
                pass
        return None
    
    @staticmethod
    def decode_zip_filename(file_info):
        """
        核心方法：精准解码 ZIP 中的文件名，消除中文乱码。
        支持 Windows (GBK/GB18030/CP936)、macOS/Linux/微信 (UTF-8)、繁体 (Big5)。
        file_info 可以是 zipfile.ZipInfo 对象，也可以是文件名字符串。
        """
        flag_bits = getattr(file_info, 'flag_bits', 0)
        raw_name = getattr(file_info, 'filename', file_info)
        
        if not raw_name:
            return ""

        can_cp437 = False
        try:
            raw_bytes = raw_name.encode('cp437')
            can_cp437 = True
        except UnicodeEncodeError:
            can_cp437 = False

        if can_cp437:
            has_cjk_raw = any('\u4e00' <= c <= '\u9fff' for c in raw_name)
            
            # UTF-8 校验极其严格，优先尝试 UTF-8；若失败（如 Windows GBK/CP936）则尝试 GB18030，再尝试 Big5
            candidates = ['utf-8', 'gb18030', 'big5', 'shift_jis']
            for enc in candidates:
                try:
                    decoded = raw_bytes.decode(enc)
                    has_cjk_decoded = any('\u4e00' <= c <= '\u9fff' for c in decoded)
                    if has_cjk_decoded and not has_cjk_raw:
                        return unicodedata.normalize('NFC', decoded)
                except (UnicodeDecodeError, LookupError):
                    continue

            # 智能检测兜底
            detected = EncodingHandler.detect_encoding(raw_bytes)
            if detected:
                try:
                    decoded = raw_bytes.decode(detected)
                    if any('\u4e00' <= c <= '\u9fff' for c in decoded) and not has_cjk_raw:
                        return unicodedata.normalize('NFC', decoded)
                except Exception:
                    pass

            # 若不含汉字（如纯英文），若可按 UTF-8 解码则返回
            try:
                decoded = raw_bytes.decode('utf-8')
                return unicodedata.normalize('NFC', decoded)
            except (UnicodeDecodeError, LookupError):
                pass

        return unicodedata.normalize('NFC', raw_name)

    @staticmethod
    def decode_tar_member(member):
        """解码 TAR 包中的成员文件名，防止中文乱码"""
        name = getattr(member, 'name', member)
        if not name:
            return ""

        try:
            # 如果 tarfile 使用了 surrogateescape，尝试通过 surrogate 还原原始字节
            raw_bytes = name.encode('utf-8', 'surrogateescape')
            for enc in ['utf-8', 'gb18030', 'gbk', 'big5']:
                try:
                    return unicodedata.normalize('NFC', raw_bytes.decode(enc))
                except (UnicodeDecodeError, LookupError):
                    continue
        except Exception:
            pass

        return unicodedata.normalize('NFC', name)

    @staticmethod
    def safe_relpath(path_str):
        """
        创建安全的相对路径：防止跨目录攻击（..），去除 Windows/Linux 非法字符，同时保留正常子目录结构
        """
        if not path_str:
            return ""
        
        clean_path = path_str.replace('\\', '/')
        segments = clean_path.split('/')
        safe_segments = []
        
        illegal_chars = re.compile(r'[<>:"|?*\x00-\x1f]')
        
        for seg in segments:
            seg = seg.strip()
            if not seg or seg == '.' or seg == '..':
                continue
            safe_seg = illegal_chars.sub('_', seg)
            if safe_seg:
                safe_segments.append(safe_seg)
                
        return os.path.sep.join(safe_segments)

    @staticmethod
    def decode_filename(filename_bytes, archive_encoding=None):
        """解码文件名字节数据"""
        if archive_encoding:
            try:
                return unicodedata.normalize('NFC', filename_bytes.decode(archive_encoding))
            except (UnicodeDecodeError, LookupError):
                pass
        
        for encoding in EncodingHandler.ENCODINGS:
            try:
                return unicodedata.normalize('NFC', filename_bytes.decode(encoding))
            except (UnicodeDecodeError, LookupError):
                continue
        
        try:
            return unicodedata.normalize('NFC', filename_bytes.decode('utf-8', errors='replace'))
        except Exception:
            return unicodedata.normalize('NFC', filename_bytes.decode('ascii', errors='replace'))
    
    @staticmethod
    def safe_filename(filename):
        """创建单个文件的安全文件名（替换路径符号与非法字符）"""
        unsafe_chars = ['<', '>', ':', '"', '|', '?', '*', '\\', '/']
        safe_name = filename
        for char in unsafe_chars:
            safe_name = safe_name.replace(char, '_')
        safe_name = ''.join(char for char in safe_name if ord(char) >= 32)
        if len(safe_name) > 255:
            name, ext = os.path.splitext(safe_name)
            safe_name = name[:255-len(ext)] + ext
        return safe_name

class ConversionService:
    """文档转换服务"""
    
    def __init__(self, convert_dir, db_manager: DatabaseManager = None):
        self.convert_to = convert_dir
        self.db_manager = db_manager

    def convert_to_pdf(self, file_path):
        """将文档和图片转换为PDF，并保存到专门的转换目录"""
        try:
            file_name = Path(file_path).stem
            # 使用专门的转换目录
            pdf_path = os.path.join(self.convert_to, f"{file_name}.pdf")
            
            # 检查是否已存在同名PDF
            if os.path.exists(pdf_path):
                # 检查时间戳，如果原文件更新则重新转换
                if os.path.getmtime(file_path) <= os.path.getmtime(pdf_path):
                    logging.info(f"PDF文件已存在，跳过转换: {pdf_path}")
                    return pdf_path
                else:
                    logging.info(f"原文件已更新，重新转换: {file_path}")
            
            file_ext = Path(file_path).suffix.lower()
            
            # 处理文档文件
            if file_ext in {'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}:
                return self._convert_office_to_pdf(file_path, pdf_path)
                
            # 处理文本文件
            elif file_ext == '.txt':
                return self._convert_text_to_pdf(file_path, pdf_path)
                
            # 处理图片文件
            elif file_ext in {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}:
                return self._convert_image_to_pdf(file_path, pdf_path)
                
            else:
                logging.warning(f"不支持转换的文件类型: {file_ext}")
                return None
                
        except Exception as e:
            error_msg = f"转换过程中发生错误: {str(e)}"
            logging.error(error_msg)
            if self.db_manager:
                self.db_manager.add_conversion_record(
                    original_file=file_path,
                    pdf_file=pdf_path if 'pdf_path' in locals() else '',
                    status='failed',
                    error_message=error_msg
                )
            return None

    def _convert_text_to_pdf(self, file_path, pdf_path):
        """使用enscript将文本文件转换为PDF"""
        try:
            # 首先检查文件编码
            try:
                with open(file_path, 'rb') as f:
                    raw_data = f.read()
                    encoding = chardet.detect(raw_data)['encoding'] or 'utf-8'
            except:
                encoding = 'utf-8'
            
            # 使用enscript转换
            cmd = f"enscript '{file_path}' --output=- --font=Courier10 --encoding={encoding} | ps2pdf - '{pdf_path}'"
            
            logging.info(f"执行文本转换命令: {cmd}")
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            
            if result.returncode == 0 and os.path.exists(pdf_path):
                logging.info(f"文本文件转换成功: {file_path} -> {pdf_path}")
                if self.db_manager:
                    self.db_manager.add_conversion_record(
                        original_file=file_path,
                        pdf_file=pdf_path,
                        status='success'
                    )
                return pdf_path
            else:
                error_msg = f"文本转换失败: {result.stderr}"
                logging.error(error_msg)
                if self.db_manager:
                    self.db_manager.add_conversion_record(
                        original_file=file_path,
                        pdf_file=pdf_path,
                        status='failed',
                        error_message=error_msg
                    )
                return None
                
        except subprocess.CalledProcessError as e:
            error_msg = f"文本转换过程出错: {str(e)}"
            logging.error(error_msg)
            if self.db_manager:
                self.db_manager.add_conversion_record(
                    original_file=file_path,
                    pdf_file=pdf_path,
                    status='failed',
                    error_message=error_msg
                )
            return None

    def _convert_image_to_pdf(self, file_path, pdf_path):
        """将图片文件转换为PDF"""
        try:
            # 方法1: 使用ImageMagick的convert命令
            try:
                cmd = ['convert', file_path, pdf_path]
                logging.info(f"执行图片转换命令: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                
                if result.returncode == 0 and os.path.exists(pdf_path):
                    logging.info(f"图片转换成功: {file_path} -> {pdf_path}")
                    if self.db_manager:
                        self.db_manager.add_conversion_record(
                            original_file=file_path,
                            pdf_file=pdf_path,
                            status='success'
                        )
                    return pdf_path
            except (subprocess.CalledProcessError, FileNotFoundError):
                # ImageMagick不可用，尝试其他方法
                pass
            
            # 方法2: 使用img2pdf Python库（如果可用）
            try:
                import img2pdf
                with open(file_path, "rb") as img_file:
                    img_data = img_file.read()
                
                with open(pdf_path, "wb") as pdf_file:
                    pdf_file.write(img2pdf.convert(img_data))
                
                if os.path.exists(pdf_path):
                    logging.info(f"图片转换成功 (img2pdf): {file_path} -> {pdf_path}")
                    if self.db_manager:
                        self.db_manager.add_conversion_record(
                            original_file=file_path,
                            pdf_file=pdf_path,
                            status='success'
                        )
                    return pdf_path
            except ImportError:
                logging.warning("img2pdf库未安装，无法使用该方法转换图片")
            except Exception as e:
                logging.error(f"img2pdf转换失败: {str(e)}")
            
            # 方法3: 使用Python的PIL库（如果可用）
            try:
                from PIL import Image
                image = Image.open(file_path)
                if image.mode == 'RGBA':
                    image = image.convert('RGB')
                image.save(pdf_path, "PDF", resolution=100.0)
                
                if os.path.exists(pdf_path):
                    logging.info(f"图片转换成功 (PIL): {file_path} -> {pdf_path}")
                    if self.db_manager:
                        self.db_manager.add_conversion_record(
                            original_file=file_path,
                            pdf_file=pdf_path,
                            status='success'
                        )
                    return pdf_path
            except ImportError:
                logging.warning("PIL库未安装，无法使用该方法转换图片")
            except Exception as e:
                logging.error(f"PIL转换失败: {str(e)}")
            
            error_msg = "所有图片转换方法都失败了"
            logging.error(error_msg)
            if self.db_manager:
                self.db_manager.add_conversion_record(
                    original_file=file_path,
                    pdf_file=pdf_path,
                    status='failed',
                    error_message=error_msg
                )
            return None
            
        except Exception as e:
            error_msg = f"图片转换过程中发生错误: {str(e)}"
            logging.error(error_msg)
            if self.db_manager:
                self.db_manager.add_conversion_record(
                    original_file=file_path,
                    pdf_file=pdf_path,
                    status='failed',
                    error_message=error_msg
                )
            return None

    def _convert_office_to_pdf(self, file_path, pdf_path):
        """使用LibreOffice转换Office文档为PDF"""
        try:
            cmd = [
                'libreoffice', '--headless', '--convert-to', 'pdf',
                '--outdir', self.convert_to, file_path
            ]
            
            logging.info(f"执行Office转换命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            if result.returncode == 0:
                # 检查实际生成的PDF文件
                expected_pdf = pdf_path
                if not os.path.exists(expected_pdf):
                    # 查找转换目录中最近创建的PDF文件
                    pdf_files = [f for f in os.listdir(self.convert_to) 
                                if f.endswith('.pdf') and 
                                os.path.getctime(os.path.join(self.convert_to, f)) > time.time() - 10]
                    if pdf_files:
                        expected_pdf = os.path.join(self.convert_to, pdf_files[0])
                
                if os.path.exists(expected_pdf):
                    logging.info(f"Office文档转换成功: {file_path} -> {expected_pdf}")
                    if self.db_manager:
                        self.db_manager.add_conversion_record(
                            original_file=file_path,
                            pdf_file=expected_pdf,
                            status='success'
                        )
                    return expected_pdf
                else:
                    error_msg = "转换成功但未找到生成的PDF文件"
                    logging.error(error_msg)
                    if self.db_manager:
                        self.db_manager.add_conversion_record(
                            original_file=file_path,
                            pdf_file=pdf_path,
                            status='failed',
                            error_message=error_msg
                        )
                    return None
            else:
                error_msg = f"Office转换失败: {result.stderr}"
                logging.error(error_msg)
                if self.db_manager:
                    self.db_manager.add_conversion_record(
                        original_file=file_path,
                        pdf_file=pdf_path,
                        status='failed',
                        error_message=error_msg
                    )
                return None
        except Exception as e:
             logging.error(f"Office转换异常: {e}")
             return None
