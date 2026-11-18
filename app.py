import os
from pathlib import Path

from waitress import serve

from image_lab import create_app
from sphereview.server import register

data = Path(os.environ.get('SPHEREVIEW_DATA_DIR', 'data'))
app = create_app(data / 'preparation')
register(app, data / 'panoramas')

if __name__ == '__main__':
    serve(app, host=os.environ.get('SPHEREVIEW_HOST', '127.0.0.1'),
          port=int(os.environ.get('PORT', '18937')), threads=4)
