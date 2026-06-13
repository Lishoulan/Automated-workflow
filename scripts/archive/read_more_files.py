
# 读取更多文件内容进行分析
files = [
    r"f:\Automated-workflow-main\src\toutiao_content_factory\services\agent_workflow.py",
    r"f:\Automated-workflow-main\src\toutiao_content_factory\services\wechat_service.py",
]

for file_path in files:
    print(f"\n{'='*80}")
    print(f"FILE: {file_path}")
    print(f"{'='*80}\n")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 打印前 8000 字符
    print(content[:8000])
    if len(content) > 8000:
        print("\n... (truncated)")
