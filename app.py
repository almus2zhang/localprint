#!/usr/bin/env python3
"""
文件监控与打印管理系统
功能：监控目录、自动转换文档为PDF（不打印）
支持日期折叠
"""

import eventlet
eventlet.monkey_patch()

import os
import time
import threading
import logging
from datetime import datetime, date
from collections import defaultdict
from flask import Flask, render_template, request, jsonify, send_file
from flask_socketio import SocketIO
from watchdog.observers import Observer

from config import SECRET_KEY, DEFAULT_WATCH_DIR, LOG_FILE_PATH
from database import DatabaseManager
from services.file_monitor import FileHandler
from services.conversion import ConversionService
from services.print_manager import PrintService

# Flask应用
# Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(app)

# 初始化服务
db_manager = DatabaseManager()
print_service = PrintService(db_manager)
conversion_service = ConversionService(
    convert_dir=os.path.join(DEFAULT_WATCH_DIR, "converted_pdfs"),
    db_manager=db_manager
)

# 全局变量
file_handler = None
observer = None

# --- 辅助函数 ---

def parse_datetime(datetime_str):
    """解析日期时间字符串"""
    if isinstance(datetime_str, datetime):
        return datetime_str
    
    formats = [
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d'
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(datetime_str, fmt)
        except ValueError:
            continue
    return datetime.now()

def group_by_date(records, date_field_index):
    """按日期分组记录"""
    grouped = defaultdict(list)
    today = date.today()
    
    for record in records:
        date_value = record[date_field_index]
        record_datetime = parse_datetime(date_value)
        record_date = record_datetime.date()
        
        delta = today - record_date
        if delta.days == 0:
            date_display = "今天"
        elif delta.days == 1:
            date_display = "昨天"
        else:
            date_display = record_date.strftime('%Y年%m月%d日')
        
        grouped[date_display].append(record)
    
    return dict(grouped)

def group_files_by_date(files):
    """按修改日期分组文件"""
    grouped = defaultdict(list)
    today = date.today()
    
    for file in files:
        modified_date = file['modified'].date()
        delta = today - modified_date
        if delta.days == 0:
            date_display = "今天"
        elif delta.days == 1:
            date_display = "昨天"
        else:
            date_display = modified_date.strftime('%Y年%m月%d日')
        
        grouped[date_display].append(file)
    
    return dict(grouped)

# --- 路由定义 ---

@app.route('/')
def index():
    """主页"""
    global file_handler
    
    files = []
    if file_handler and os.path.exists(file_handler.watch_directory):
        for root, dirs, filenames in os.walk(file_handler.watch_directory):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                # 跳过转换目录和解压目录里的临时文件，避免界面混乱? 
                # 这里简单判断，只要handler认为是可转换或可打印的就列出
                if file_handler._is_printable_file(file_path): 
                     files.append({
                        'name': filename,
                        'path': file_path,
                        'size': os.path.getsize(file_path),
                        'modified': datetime.fromtimestamp(os.path.getmtime(file_path)),
                        'type': 'printable'
                    })
                elif file_handler._is_convertible_file(file_path):
                    files.append({
                        'name': filename,
                        'path': file_path,
                        'size': os.path.getsize(file_path),
                        'modified': datetime.fromtimestamp(os.path.getmtime(file_path)),
                        'type': 'convertible'
                    })

    files.sort(key=lambda x: x['modified'], reverse=True)
    files_by_date = group_files_by_date(files)
    
    # 提取PDF文件列表
    pdf_files = [f for f in files if f['type'] == 'printable']
    pdf_files_by_date = group_files_by_date(pdf_files)
    
    # 历史记录
    history = db_manager.get_print_history(100)
    history_by_date = group_by_date(history, 3) # print_time index 3
    
    conversion_history = db_manager.get_conversion_history(30)
    
    return render_template('index.html', 
                         files_by_date=files_by_date,
                         history_by_date=history_by_date,
                         printable_files_by_date=pdf_files_by_date,
                         conversion_history=conversion_history)

@app.route('/api/view_file')
def api_view_file():
    """API: 预览/下载文件"""
    file_path = request.args.get('path')
    if not file_path or not os.path.exists(file_path):
        return "File not found", 404
    
    # 简单的安全校验，确保只能访问 DEFAULT_WATCH_DIR 下的文件
    # 因为路径可能是反斜杠或斜杠，做统一处理
    abs_path = os.path.abspath(file_path)
    base_dir = os.path.abspath(DEFAULT_WATCH_DIR)
    if not abs_path.startswith(base_dir):
        return "Access denied", 403
        
    return send_file(abs_path)

# --- 跨域处理 (CORS) ---
@app.before_request
def handle_options():
    if request.method == 'OPTIONS':
        resp = app.make_default_options_response()
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS, PUT, DELETE'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
        return resp

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS, PUT, DELETE'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    return response

@app.route('/api/status')
def api_status():
    """API: 服务状态检查与打印机状态"""
    printers = []
    try:
        import subprocess
        cmd = ['lpstat', '-p']
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.stdout:
            for line in res.stdout.splitlines():
                if line.startswith('printer'):
                    printers.append(line.strip())
    except Exception as e:
        printers = [str(e)]
    return jsonify({
        'status': 'online',
        'server_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'printers': printers,
        'watch_dir': DEFAULT_WATCH_DIR
    })

@app.route('/api/upload', methods=['POST'])
def api_upload():
    """API: 上传文件（支持微信小程序等客户端文件上传与立即打印）"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未检测到上传文件'}), 400
    
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'success': False, 'message': '文件名为空'}), 400

    # 优先获取客户端真实文件名（微信小程序上传时会将本地临时文件命名为 tmp_xxx）
    client_name = request.form.get('original_filename') or request.form.get('filename')
    raw_name = client_name.strip() if client_name else file.filename
    from services.conversion import EncodingHandler
    safe_name = EncodingHandler.safe_filename(os.path.basename(raw_name))
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    save_filename = f"{timestamp}_{safe_name}"
    save_path = os.path.join(DEFAULT_WATCH_DIR, save_filename)

    try:
        file.save(save_path)
    except Exception as e:
        return jsonify({'success': False, 'message': f'文件保存失败: {str(e)}'}), 500

    auto_print = request.form.get('auto_print', 'false').lower() == 'true'
    print_type = request.form.get('print_type', 'two-sided-long-edge')
    page_ranges = request.form.get('page_ranges', '')
    try:
        copies = int(request.form.get('copies', 1))
    except (ValueError, TypeError):
        copies = 1

    # 检查是否为压缩包文件
    if file_handler._is_archive_file(save_path):
        if auto_print:
            file_handler.auto_print_files.add(save_path)
        file_handler.file_queue.append(save_path)
        return jsonify({
            'success': True,
            'message': '压缩包已上传，正在后台自动解压' + ('并打印内部文档' if auto_print else '中'),
            'file_path': save_path,
            'is_archive': True
        })

    if auto_print:
        final_path = save_path
        if file_handler._is_convertible_file(save_path) and not file_handler._is_printable_file(save_path):
            pdf_path = conversion_service.convert_to_pdf(save_path)
            if pdf_path and os.path.exists(pdf_path):
                file_handler.processed_files.add(pdf_path)
                final_path = pdf_path
            else:
                return jsonify({'success': False, 'message': '文件转换PDF失败'}), 500

        queue_id = db_manager.add_to_print_queue(
            filename=os.path.basename(final_path),
            filepath=final_path,
            print_type=print_type
        )

        def print_in_background():
            success = file_handler._print_file(final_path, print_type, page_ranges, copies=copies)
            db_manager.update_queue_status(queue_id, 'completed' if success else 'failed')

        threading.Thread(target=print_in_background, daemon=True).start()

        return jsonify({
            'success': True,
            'message': '文件上传成功并已提交打印',
            'file_path': final_path,
            'queue_id': queue_id
        })

    return jsonify({
        'success': True,
        'message': '文件上传成功',
        'file_path': save_path,
        'filename': raw_name
    })

@app.route('/api/print', methods=['POST'])
def api_print():
    """API: 打印文件"""
    data = request.json or {}
    file_path = data.get('file_path')
    print_type = data.get('print_type', 'two-sided-short-edge')
    page_ranges = data.get('page_ranges', '')
    try:
        copies = int(data.get('copies', 1))
    except (ValueError, TypeError):
        copies = 1
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'success': False, 'message': '文件不存在'})
    
    # 转换逻辑
    file_ext = os.path.splitext(file_path)[1].lower()
    if file_handler._is_convertible_file(file_path) and not file_handler._is_printable_file(file_path):
         # 调用转换服务
         pdf_path = conversion_service.convert_to_pdf(file_path)
         if pdf_path and os.path.exists(pdf_path):
             file_handler.processed_files.add(pdf_path)
             file_path = pdf_path
         else:
             return jsonify({'success': False, 'message': '文件转换失败'})
              
    # 添加到数据库队列
    queue_id = db_manager.add_to_print_queue(
        filename=os.path.basename(file_path),
        filepath=file_path,
        print_type=print_type
    )
    
    # 后台打印
    def print_in_background():
        success = file_handler._print_file(file_path, print_type, page_ranges, copies=copies)
        db_manager.update_queue_status(queue_id, 'completed' if success else 'failed')
    
    threading.Thread(target=print_in_background, daemon=True).start()
    return jsonify({'success': True, 'message': '打印任务已提交'})

@app.route('/api/inspect_pages', methods=['POST'])
def api_inspect_pages():
    """API: 解析上传文档的总页数"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未检测到文件', 'pages': 1}), 400
    
    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'success': False, 'message': '文件名为空', 'pages': 1}), 400

    client_name = request.form.get('original_filename') or request.form.get('filename')
    raw_name = client_name.strip() if client_name else file.filename
    from services.conversion import EncodingHandler
    safe_name = EncodingHandler.safe_filename(os.path.basename(raw_name))
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    save_filename = f"inspect_{timestamp}_{safe_name}"
    save_path = os.path.join(DEFAULT_WATCH_DIR, save_filename)

    try:
        file.save(save_path)
    except Exception as e:
        return jsonify({'success': False, 'message': f'保存失败: {str(e)}', 'pages': 1}), 500

    pages = file_handler._get_document_page_count(save_path)
    
    # 若普通 Office 文档未直接解析出页数，尝试快速通过 LibreOffice 转换 PDF 提取精确页数
    file_ext = os.path.splitext(save_path)[1].lower()
    if pages == 1 and file_handler._is_convertible_file(save_path) and not file_handler._is_printable_file(save_path):
        try:
            pdf_path = conversion_service.convert_to_pdf(save_path)
            if pdf_path and os.path.exists(pdf_path):
                file_handler.processed_files.add(pdf_path)
                pages = file_handler._get_document_page_count(pdf_path)
        except Exception:
            pass

    return jsonify({
        'success': True,
        'filename': raw_name,
        'pages': max(1, pages),
        'file_path': save_path
    })

@app.route('/api/archive/inspect', methods=['POST'])
def api_archive_inspect():
    """API: 上传并解压压缩包，返回内部可打印文件列表供小程序挑选"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未检测到上传文件'}), 400
    
    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'success': False, 'message': '文件名为空'}), 400

    client_name = request.form.get('original_filename') or request.form.get('filename')
    raw_name = client_name.strip() if client_name else file.filename
    from services.conversion import EncodingHandler
    safe_name = EncodingHandler.safe_filename(os.path.basename(raw_name))
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    save_filename = f"{timestamp}_{safe_name}"
    save_path = os.path.join(DEFAULT_WATCH_DIR, save_filename)

    try:
        file.save(save_path)
    except Exception as e:
        return jsonify({'success': False, 'message': f'文件保存失败: {str(e)}'}), 500

    # 专门解压目录
    safe_dir_name = f"inspect_{timestamp}_{os.path.splitext(base_name)[0]}"
    extract_dir = os.path.join(file_handler.extract_to, safe_dir_name)
    os.makedirs(extract_dir, exist_ok=True)

    extracted_ok = False
    file_ext = os.path.splitext(save_path)[1].lower()
    if file_ext == '.zip' or save_path.lower().endswith('.zip'):
        extracted_ok = file_handler._extract_zip(save_path, extract_dir, auto_print=False)
    elif file_ext in {'.tar', '.gz', '.bz2'} or save_path.lower().endswith(('.tar.gz', '.tar.bz2')):
        mode = 'r:gz' if save_path.lower().endswith(('.gz', '.tar.gz')) else ('r:bz2' if save_path.lower().endswith(('.bz2', '.tar.bz2')) else 'r')
        extracted_ok = file_handler._extract_tar(save_path, extract_dir, mode=mode, auto_print=False)
    else:
        # 兼容其他常见压缩格式或无扩展名尝试
        extracted_ok = file_handler._extract_zip(save_path, extract_dir, auto_print=False)

    if not extracted_ok:
        return jsonify({'success': False, 'message': '解压失败，请检查压缩包格式是否支持或损坏'}), 400

    # 扫描解压出的可打印文件
    file_list = []
    idx = 1
    for root, dirs, filenames in os.walk(extract_dir):
        for fname in sorted(filenames):
            if fname.startswith('.') or '__MACOSX' in root or fname.lower() == 'thumbs.db':
                continue
            fpath = os.path.join(root, fname)
            if not os.path.isfile(fpath):
                continue
            
            fsize = os.path.getsize(fpath)
            size_kb = fsize / 1024
            size_text = f"{size_kb / 1024:.2f} MB" if size_kb > 1024 else f"{size_kb:.1f} KB"
            
            is_printable = file_handler._is_printable_file(fpath)
            is_convertible = file_handler._is_convertible_file(fpath)
            
            if is_printable or is_convertible:
                pages = file_handler._get_document_page_count(fpath) if hasattr(file_handler, '_get_document_page_count') else 1
                rel_path = os.path.relpath(fpath, extract_dir).replace('\\', '/')
                display_filename = rel_path if '/' in rel_path else fname
                file_list.append({
                    'id': str(idx),
                    'filename': display_filename,
                    'filepath': fpath,
                    'size_text': size_text,
                    'is_pdf': is_printable,
                    'pages': pages,
                    'selected': True
                })
                idx += 1

    return jsonify({
        'success': True,
        'archive_name': raw_name,
        'count': len(file_list),
        'files': file_list
    })

@app.route('/api/print_multiple', methods=['POST'])
def api_print_multiple():
    """API: 批量打印"""
    data = request.json or {}
    file_paths = data.get('file_paths', [])
    print_type = data.get('print_type', 'two-sided-long-edge')
    page_ranges = data.get('page_ranges', '')
    try:
        copies = int(data.get('copies', 1))
    except (ValueError, TypeError):
        copies = 1
    
    if not file_paths:
        return jsonify({'success': False, 'message': '未选择文件'})
        
    success_count = 0
    failed_files = []
    
    for file_path in file_paths:
        if not os.path.exists(file_path):
             failed_files.append(f"{os.path.basename(file_path)}: 不存在")
             continue
             
        # 转换
        final_file_path = file_path
        if file_handler._is_convertible_file(file_path) and not file_handler._is_printable_file(file_path):
             pdf_path = conversion_service.convert_to_pdf(file_path)
             if pdf_path and os.path.exists(pdf_path):
                 final_file_path = pdf_path
             else:
                 failed_files.append(f"{os.path.basename(file_path)}: 转换失败")
                 continue
        
        queue_id = db_manager.add_to_print_queue(
            filename=os.path.basename(final_file_path),
            filepath=final_file_path,
            print_type=print_type
        )
        
        def print_bg(fp=final_file_path, qid=queue_id):
            success = file_handler._print_file(fp, print_type, page_ranges, copies=copies)
            db_manager.update_queue_status(qid, 'completed' if success else 'failed')
        
        threading.Thread(target=print_bg, daemon=True).start()
        success_count += 1
        
    message = f"成功提交 {success_count} 个任务"
    if failed_files:
        message += f"，失败 {len(failed_files)} 个"
        
    return jsonify({
        'success': True,
        'message': message,
        'success_count': success_count,
        'failed_count': len(failed_files)
    })

@app.route('/api/clean_converted', methods=['POST'])
def api_clean_converted():
    """API: 清理旧转换文件"""
    try:
        data = request.json
        days = data.get('days', 7) if data else 7
        cutoff = time.time() - (days * 86400)
        deleted = 0
        
        convert_dir = file_handler.convert_to
        if os.path.exists(convert_dir):
            for fname in os.listdir(convert_dir):
                fpath = os.path.join(convert_dir, fname)
                if os.path.isfile(fpath) and os.path.getctime(fpath) < cutoff:
                    os.remove(fpath)
                    deleted += 1
        return jsonify({'success': True, 'message': f'已清理 {deleted} 个文件'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/reprint', methods=['POST'])
def api_reprint():
    """API: 重新打印"""
    return api_print() # 逻辑相同

@app.route('/api/history')
def api_history():
    import re
    res = []
    seen_files = set()

    # 1. 如果 CUPS 当前有正在打印的任务，先插入到最顶部
    try:
        active_queue = print_service.get_print_queue()
        for j in active_queue:
            if j.get('status') == 'printing':
                fname = j.get('filename', '未知文件')
                if fname.startswith('tmp_'):
                    ext = os.path.splitext(fname)[1]
                    fname = f"微信文档{ext}" if ext else "微信文档"
                res.append({
                    'id': j.get('id'),
                    'filename': fname,
                    'filepath': j.get('filepath', ''),
                    'print_time': str(j.get('added_time', '')),
                    'pages': j.get('pages', 1),
                    'print_type': j.get('print_type', 'two-sided-long-edge'),
                    'status': 'printing',
                    'error_message': ''
                })
                seen_files.add(j.get('filename'))
    except Exception as e:
        logging.error(f"获取实时打印任务异常: {e}")

    # 2. 从数据库获取打印历史
    history = db_manager.get_print_history(100)
    for i in history:
        raw_name = i[1]
        clean_name = re.sub(r'^\d{8}[_]?\d{4,6}_', '', raw_name)
        if clean_name.startswith('tmp_'):
            ext = os.path.splitext(clean_name)[1]
            clean_name = f"微信文档{ext}" if ext else "微信文档"
        pages = i[4] if i[4] and i[4] > 0 else 1
        res.append({
            'id': i[0],
            'filename': clean_name,
            'filepath': i[2],
            'print_time': str(i[3])[:19] if i[3] else '',
            'pages': pages,
            'print_type': i[5],
            'status': i[6] or 'completed',
            'error_message': i[8] or ''
        })

    return jsonify(res)

@app.route('/api/file_history')
def api_file_history():
    history = db_manager.get_file_history(100)
    res = [{'id': i[0], 'filename': i[1], 'filepath': i[2], 'file_type': i[3], 
            'process_time': i[4], 'status': i[5]} for i in history]
    return jsonify(res)

@app.route('/api/conversion_history')
def api_conversion_history():
    history = db_manager.get_conversion_history(50)
    res = [{'id': i[0], 'original_file': i[1], 'pdf_file': i[2], 'convert_time': i[3], 
            'status': i[4], 'error_message': i[5] or ''} for i in history]
    return jsonify(res)

@app.route('/api/queue')
def api_queue():
    queue = print_service.get_print_queue()
    res = []
    for i in queue:
        if isinstance(i, dict):
            res.append({
                'id': i.get('id'),
                'filename': i.get('filename', '未知文件'),
                'pages': i.get('pages', 1),
                'filepath': i.get('filepath', ''),
                'print_type': i.get('print_type', 'two-sided-long-edge'),
                'added_time': str(i.get('added_time', '')),
                'status': i.get('status', 'pending')
            })
        elif isinstance(i, (list, tuple)):
            pages = i[6] if len(i) > 6 else 1
            res.append({
                'id': i[0],
                'filename': i[1],
                'pages': pages,
                'filepath': i[2],
                'print_type': i[3],
                'added_time': str(i[4]),
                'status': i[5]
            })
    return jsonify(res)
    return jsonify(res)

@app.route('/api/clear_file_history', methods=['POST'])
def api_clear_file_history():
    data = request.json
    success, msg = print_service.clear_file_history_by_date(data.get('date'))
    return jsonify({'success': success, 'message': msg})

@app.route('/api/clear_print_history', methods=['POST'])
def api_clear_print_history():
    data = request.json
    success, msg = print_service.clear_print_history_by_date(data.get('date'))
    return jsonify({'success': success, 'message': msg})

@app.route('/api/clear_conversion_history', methods=['POST'])
def api_clear_conversion_history():
    count = db_manager.clear_all_conversion_history()
    return jsonify({'success': True, 'message': f'已清除 {count} 条记录'})

@app.route('/api/clear_print_queue', methods=['POST'])
def api_clear_print_queue():
    count = db_manager.clear_pending_print_queue()
    return jsonify({'success': True, 'message': f'已清除 {count} 条记录'})

@app.route('/api/cancel_all_prints', methods=['POST'])
def api_cancel_all_prints():
    success, msg = print_service.cancel_all_print_jobs()
    return jsonify({'success': success, 'message': msg})

@app.route('/api/cancel_print/<job_id>', methods=['POST'])
def api_cancel_print(job_id):
    # job_id 可能是 int 也可能是 string (CUPS job id)
    success, msg = print_service.cancel_print_job(job_id)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/debug/queue')
def api_debug_queue():
    import subprocess
    cups_info = ""
    try:
        cmd = ['lpstat', '-o']
        res = subprocess.run(cmd, capture_output=True, text=True)
        cups_info = res.stdout
    except Exception as e:
        cups_info = str(e)
    
    return jsonify({
        'cups_queue': cups_info,
        'database_queue': db_manager.get_pending_print_queue(),
        'database_path': db_manager.db_path
    })

def start_file_monitor():
    """启动文件监控"""
    global file_handler, observer
    
    # 确保监控目录存在
    if not os.path.exists(DEFAULT_WATCH_DIR):
        try:
            os.makedirs(DEFAULT_WATCH_DIR)
        except Exception as e:
            logging.error(f"无法创建监控目录: {e}")
            return

    file_handler = FileHandler(
        watch_directory=DEFAULT_WATCH_DIR, 
        db_manager=db_manager,
        conversion_service=conversion_service,
        socketio=socketio
    )
    observer = Observer()
    observer.schedule(file_handler, DEFAULT_WATCH_DIR, recursive=True)
    observer.start()
    logging.info(f"Monitor started on {DEFAULT_WATCH_DIR}")

if __name__ == '__main__':
    # 启动监控
    start_file_monitor()
    
    # 启动Web服务
    # host='0.0.0.0' 允许外部访问
    # 使用 socketio.run 代替 app.run
    socketio.run(app, host='0.0.0.0', port=5005, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
