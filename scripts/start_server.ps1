$env:PYTHONPATH="F:\Automated-workflow-main\src"
Set-Location "F:\Automated-workflow-main"
.\.venv\Scripts\python.exe -m uvicorn toutiao_content_factory.web.app:create_app_factory --host 0.0.0.0 --port 8000 --factory
