import os
import logging

# Base Directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Database Configuration
DB_PATH = os.path.join(BASE_DIR, 'print_history.db')

# File Monitoring Configuration
DEFAULT_WATCH_DIR = os.path.join(BASE_DIR, 'file')

# Logging Configuration
LOG_FILE_PATH = '/var/log/file_monitor.log'
# Fallback if permission denied (e.g. on Windows without admin)
if not os.access(os.path.dirname(LOG_FILE_PATH) if os.path.dirname(LOG_FILE_PATH) else '.', os.W_OK):
    LOG_FILE_PATH = os.path.join(BASE_DIR, 'app.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE_PATH),
        logging.StreamHandler()
    ]
)

# Flask Configuration
SECRET_KEY = 'your-secret-key-here'

# File Extensions
ARCHIVE_EXTENSIONS = {'.zip', '.tar', '.gz', '.bz2', '.7z', '.rar'}
CONVERTIBLE_EXTENSIONS = {'.doc', '.docx', '.xls', '.xlsx', '.txt', '.ppt', '.pptx', 
                         '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}

