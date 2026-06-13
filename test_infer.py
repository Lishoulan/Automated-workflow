
import sys
from pathlib import Path

# 项目根目录
project_root = Path(__file__).resolve().parent
src_dir = project_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# 导入需要的类
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService
from toutiao_content_factory.config import load_settings

# 测试函数
def test_infer_track():
    settings = load_settings(project_root)
    
    # 创建 GrowthOpsService 实例
    from unittest.mock import Mock
    logger = Mock()
    service = GrowthOpsService(settings, logger)
    
    # 测试用例
    test_cases = [
        "宇树、智元的线下门店叙事：具身智能正入迈进商业周期",
        "人形机器人5万块一车甩卖，三年前还要60万",
        "18国学员齐聚三亚 共探科技小院国际化发展路径",
        "国际油价大跌",
        "国足输球了"
    ]
    
    print("测试 infer_title_playbook_track 函数:")
    print("="*80)
    for test_text in test_cases:
        track = service.infer_title_playbook_track(test_text)
        print(f"文本: {test_text}")
        print(f"得到 track: {track}")
        print("-"*80)
    
    print()
    print("直接测试逻辑:")
    normalized = "宇树、智元的线下门店叙事：具身智能正入迈进商业周期"
    print(f"normalized: {normalized}")
    
    robot_tokens = ("机器人", "人形", "具身", "减速器", "灵巧手", "伺服", "关节电机")
    print(f"robot_tokens: {robot_tokens}")
    print(f"any in robot_tokens? {any(token in normalized for token in robot_tokens)}")
    
    tech_tokens = ["机器人", "芯片", "半导体", "储能", "光伏", "锂电", "电池", "AI", "大模型", "智能制造", "工业自动化", "人形", "具身", "减速器", "灵巧手", "伺服", "算力", "光刻机", "风电", "逆变器"]
    tech_tokens_count = sum(1 for token in tech_tokens if token in normalized)
    print(f"tech_tokens_count: {tech_tokens_count}")
    
    print(f"具身" in normalized)

if __name__ == "__main__":
    test_infer_track()
