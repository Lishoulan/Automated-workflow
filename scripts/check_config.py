import sys
from pathlib import Path
sys.path.insert(0, str(Path(r"f:\Automated-workflow-main") / "src"))
from toutiao_content_factory.config import load_settings
s = load_settings(Path(r"f:\Automated-workflow-main"), account_key="robot_notes")
print(f"publishing.enabled={s.publishing.enabled}")
print(f"publishing.auto_publish={s.publishing.auto_publish}")
print(f"wechat.enabled={s.wechat.enabled}")
print(f"wechat.auto_publish={s.wechat.auto_publish}")
key_status = "SET" if s.openai_api_key else "EMPTY"
print(f"openai_api_key={key_status}")
print(f"openai_model={s.openai_model}")
print(f"openai_base_url={s.openai_base_url}")
