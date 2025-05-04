import os
import requests
import zipfile
import io

FONT_URLS = {
    'Roboto-Bold.ttf': 'https://github.com/google/fonts/raw/main/apache/roboto/static/Roboto-Bold.ttf',
    'Roboto-Regular.ttf': 'https://github.com/google/fonts/raw/main/apache/roboto/static/Roboto-Regular.ttf'
}

def ensure_fonts_exist():
    """Ensure required fonts are available"""
    font_dir = os.path.join('assets', 'fonts')
    os.makedirs(font_dir, exist_ok=True)
    
    for font_name, font_url in FONT_URLS.items():
        font_path = os.path.join(font_dir, font_name)
        if not os.path.exists(font_path):
            try:
                print(f"Downloading font: {font_name}")
                response = requests.get(font_url)
                response.raise_for_status()
                
                with open(font_path, 'wb') as f:
                    f.write(response.content)
                print(f"Successfully downloaded: {font_name}")
            except Exception as e:
                print(f"Error downloading font {font_name}: {str(e)}")