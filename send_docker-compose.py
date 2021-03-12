import shutil
from pathlib import Path

if __name__ == '__main__':
    filename = 'docker-compose.yml'
    shutil.copyfile(src=filename, dst=Path(r'\\192.168.1.245\dietpi') / filename)
