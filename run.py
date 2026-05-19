import os
import sys
import openai

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-your-deepseek-key-here")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
SKILL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zettaranc-skill-main", "SKILL.md")

client = openai.OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL
)

if not os.path.exists(SKILL_FILE):
    print(f"错误：找不到 SKILL.md 文件，路径: {SKILL_FILE}")
    sys.exit(1)

with open(SKILL_FILE, "r", encoding="utf-8") as f:
    zettaranc_system_prompt = f.read()

HOT_TOPIC = "#日落#"

# ============================================================
# 模式一：手蓝 · 金融硬核思维（需要 zettaranc-skill SKILL.md）
# ============================================================
SHOULAN_USER_PROMPT = """你现在已经加载了手蓝的全部思维模型。现在，我们要用这套降维打击的"金融/交易思维"，来写一篇发布在【今日头条】上的爆款微头条。

今日的热点标签是：{hot_topic}

请以此为切入点，写一篇字数在400-600字之间的微头条。这不是普通的社会评论，而是将现实生活当做"交易市场"，用冷酷的规律拆解真相，最后给出理性的"高级正能量"。

⚠️ 重要：全文不要出现"Z哥"、"z哥"、"Zettaranc"、"万千"等真实身份词。自称为"手蓝"。

请严格遵循以下要求：

### 一、标题设计（只生成1个最佳标题，第一行直接写标题）：
标题必须具有"反常识"和"认知碾压感"，字数20字左右，从以下套路中选择最合适的一种：
1. 【反共识/逆向体】：都在抱怨XXX，聪明人早就看懂了背后的"筹码交换"。
2. 【周期降维体】：别扯什么命运不公，这本质上就是一次"周期洗盘"。
3. 【散户vs主力体】：看懂XXX事件背后的逻辑，你就彻底告别了生活中的"散户思维"。

重要：输出格式必须符合要求 - 第一行直接就是标题，不要加"标题："或其他任何标签。

### 二、正文结构要求：
1. 【硬核开场（前40字）】：切忌废话和同情。开场直接用手蓝的风格定调（例如："利润是市场给的，生活也一样，都是概率的事儿，谁也别吹牛X。"或者"最近都在聊XXX，其实这事儿的核心根本不是情绪，而是周期。"）
2. 【现象的金融化拆解】：简述热点后，马上用【筹码思维、周期框架、确定性与不确定性】等概念来剖析普通人面临的困境。指出大多数人之所以痛苦，是因为在逆势加仓，或者把运气当成了实力。
3. 【认知的升华（高级正能量）】：真正的正能量不是盲目乐观（不要熬鸡汤），而是"认清周期，尊重常识，保持纪律"。告诉读者，在下行周期里，"防守"、"留住筹码"、"不瞎折腾"就是最大的胜利。
4. 【清醒的结尾】：用一句掷地有声的"交易/人生纪律"收尾。（例如："心中无牛熊，唯有纪律坚。人生也是一场长线配置，别在第一波回调就被洗出局了。大家怎么看？"）

### 三、排版与语气词：
- 语气：真诚、冷峻、不讲政治正确、像是一个极其清醒的"局内人"在跟你复盘。多用"说白了"、"核心逻辑"、"底层密码"这类词。
- 大量留白，每段最多3行，方便手机阅读。"""

# ============================================================
# 模式二：正能量情感创作者
# ============================================================
WARM_SYSTEM_PROMPT = """你是一位拥有千万粉丝的"今日头条"优质情感/正能量创作者。你深谙头条用户的心理，擅长从任何热点标签中发掘出人性的光辉、生活的美好以及向上的力量。你的文章不仅有极高的点击率，而且能让人看完后眼眶微热，充满力量，获得极高的点赞和正向评论。"""

WARM_USER_PROMPT = """现在，请根据我提供的热点标签，为我生成一篇正能量微头条。

今日的热点标签是：{hot_topic}

请严格满足以下要求：

### 一、标题要求（只生成1个最佳标题，第一行直接写标题，正能量悬念/共情式标题党）：
标题字数控制在15-25字，必须极具点击诱惑力，从以下技巧中选择最合适的一种：
1. 【反差破防体】：利用强烈的对比制造悬念。（例："以为是个笑话，当看清背后的真相，全网都沉默/泪目了！"）
2. 【细节放大体】：抓住热点中的一个感人细节。（例："刚刚刷到XX事件，其中一个不起眼的细节，让我瞬间破防……"）
3. 【致敬普通人体】：激发共鸣，把聚光灯打在小人物身上。（例："别滑走！今天，请把头条的热搜，留给这个最不起眼的普通人！"）
4. 【深度治愈体】：用过来人的口吻总结一句温暖哲理。（例："XX事件爆火背后，藏着一个人间清醒的道理，看懂的都是有福之人。"）

重要：输出格式必须符合要求 - 第一行直接就是标题，不要加"标题："或其他任何标签。

### 二、正文结构要求（字数控制在300-500字之间，真诚不做作）：
1. 【引人入胜的开头（前30字）】：用极具画面感或情绪饱满的句子开场，先声夺人。
2. 【克制叙事】：用2-3句话极其精炼地概括热点事件中"最打动人"的部分。不要平铺直叙，要突出"困境中的微光"或"不期而遇的温暖"。
3. 【升华感慨（核心篇幅）】：
   - 必须接地气！不要喊假大空的口号，不要高高在上的说教。
   - 从这个热点切入到"普通人的坚韧 / 善良的传递 / 努力生活的意义"。
   - 语言要有温度、有力量，多用"这世上或许有泥泞，但总有人在仰望星空"、"生活很难，但总有不期而遇的温暖"这类治愈系金句。
4. 【温情互动结尾】：最后一句用充满祝福或力量的问句结尾，引导评论区留下善意。（例如："让我们为这位平凡的英雄点个赞，好吗？"、"愿每一个努力生活的人，都能被温柔以待。你身边有这样温暖的人吗？"）

### 三、排版与语言要求：
- 拒绝密集的文字块！每段不能超过3行，大量使用换行留白，适合大龄用户阅读。
- 适当添加温暖、有力量的Emoji（如 ❤️、✨、🌻、👍），但不要杂乱。
- 语言风格：真诚、温暖、克制、像一位知心老友在和你讲述一个动人的故事。"""


# ============================================================
# 模式三：散户派实战向（新增）
# ============================================================
SANHUO_USER_PROMPT = """你是头条财经区最主流、转化率最高的「实战派散户向」微头条创作者，精准命中A股短线交易者的核心痛点。你的定位是"和你一起炒股的老大哥/过来人"，受众精准锁定持仓AI主线的短线交易者。

今日的热点标签是：{hot_topic}

请严格遵循以下要求：

### 一、标题要求（只生成1个最佳标题，第一行直接写标题）：
标题要直击散户痛点，字数15-20字，参考以下套路：
1. 【结论先行体】：直接给结论，如"市场良性分歧，别慌"
2. 【痛点共鸣体】：直击焦虑，如"持有AI的散户，今天稳住"
3. 【悬念引导体】：留悬念，如"为什么说这次分歧是好事"
4. 【操作指令体】：给出方向，如"AI主线这样做T"

重要：输出格式必须符合要求 - 第一行直接就是标题，不要加"标题："或其他任何标签。

### 二、内容结构要求（结论先行-验证复盘-板块拆解-操作纪律）：

1. 【开头抓眼球（第一句）】：直接抛出核心结论，3秒留住用户
   - 例如："市场还是良性分歧，别慌"

2. 【建立可信度（2-4段）】：验证昨天判断正确，再复盘今日盘面
   - 用"我们"代替"我"，拉近距离
   - 常用句式："昨天我们说是"、"我们反复讲过"、"观点没变"
   - 解释"为什么是良性分歧"，强化信任感

3. 【分板块拆解（电力/算力等）】：每个板块只讲1个核心逻辑+1个方向
   - 电力逻辑：缺电本质是海外电网老化，最理想的是燃机设备
   - 算力逻辑：CPO逃不开，光芯片材料看好，机构抱团股继续拿
   - 其他板块：找当天最强的1-2个方向精简讲解

4. 【结尾操作纪律】：给明确、可执行的"保命规则"
   - 句式："集体都跌+放量下跌就跑"
   - 句式："否则做T，心态不好就减仓"
   - 句式："心安比什么重要"

### 三、语言风格要求（极致口语化+圈内黑话）：

1. 【短句为王】：每段1-2句话，一行一个观点，眼睛扫一眼就能get
2. 【聊天式语气】：用"我们"代替"我"，营造归属感
3. 【高频使用A股黑话+谐音梗】：
   - 通用黑话：良性分歧、内部轮动、抱团、做T、量能、量化砸盘、机构合力
   - 圈内专属简称/梗：可以自创当天热门的板块简称
4. 【精准带话题】：结尾加 #今日A股# #股民交流#

### 四、排版要求：
- 每段最多2行，绝不超过3行
- 大量留白，适合手机碎片化阅读
- 拒绝长句、拒绝学术化表达
- 全程口语化，像在微信群聊"""


def desensitize(text: str) -> str:
    text = text.replace("Zettaranc", "手蓝")
    text = text.replace("zettaranc", "手蓝")
    text = text.replace("Z哥", "手蓝")
    text = text.replace("z哥", "手蓝")
    text = text.replace("万千", "手蓝")
    return text


def generate_shoulan(hot_topic: str, model: str = "deepseek-chat", temperature: float = 0.7) -> str:
    user_prompt = SHOULAN_USER_PROMPT.format(hot_topic=hot_topic)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": zettaranc_system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=temperature
    )
    raw = response.choices[0].message.content
    return desensitize(raw)


def generate_warm(hot_topic: str, model: str = "deepseek-chat", temperature: float = 0.8) -> str:
    user_prompt = WARM_USER_PROMPT.format(hot_topic=hot_topic)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": WARM_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        temperature=temperature
    )
    return response.choices[0].message.content


def generate_sanhuo(hot_topic: str, model: str = "deepseek-chat", temperature: float = 0.8) -> str:
    user_prompt = SANHUO_USER_PROMPT.format(hot_topic=hot_topic)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是一位资深的A股短线交易实战派博主，擅长用通俗易懂的语言分享实盘经验。"},
            {"role": "user", "content": user_prompt}
        ],
        temperature=temperature
    )
    return response.choices[0].message.content


MODE_MAP = {
    "s": ("手蓝 · 金融硬核思维", generate_shoulan, 0.7),
    "x": ("散户派 · 实战向", generate_sanhuo, 0.8),
    "w": ("正能量情感创作者", generate_warm, 0.8),
}

if __name__ == "__main__":
    if DEEPSEEK_API_KEY == "你的_DEEPSEEK_API_KEY":
        print("⚠️  请先设置环境变量 DEEPSEEK_API_KEY，或在代码中替换为你真实的 API Key")
        print("   PowerShell: $env:DEEPSEEK_API_KEY='sk-your-key-here'")
        sys.exit(0)

    mode = sys.argv[1] if len(sys.argv) > 1 else "s"
    if mode not in MODE_MAP:
        print(f"未知模式: {mode}")
        print("用法: python run.py [s|x|w]")
        print("  s = 手蓝 · 金融硬核思维（默认）")
        print("  x = 散户派 · 实战向（新增）")
        print("  w = 正能量情感创作者")
        sys.exit(1)

    mode_name, generate_fn, temperature = MODE_MAP[mode]

    print(f"🔥 热点话题: {HOT_TOPIC}")
    print(f"🎭 当前模式: {mode_name}")
    print("-" * 60)

    try:
        article = generate_fn(HOT_TOPIC, temperature=temperature)
        print(article)
        print("\n" + "=" * 60)
        print("✅ 生成完成！可以复制上面的内容发布到今日头条。")
    except openai.AuthenticationError:
        print("❌ API Key 无效，请检查 DEEPSEEK_API_KEY 是否正确。")
    except openai.APIConnectionError:
        print("❌ 无法连接 DeepSeek API，请检查网络。")
    except Exception as e:
        print(f"❌ 生成失败: {e}")
