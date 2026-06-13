import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from toutiao_content_factory.web.app import create_app
from pathlib import Path

app = create_app(project_root=Path(__file__).parent)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
