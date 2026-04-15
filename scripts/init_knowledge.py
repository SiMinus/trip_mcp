"""初始化旅游知识库 — 灌入基础城市/景点/美食文化数据"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb
from sentence_transformers import SentenceTransformer
from agent.config import settings

DOCS = [
    # 杭州
    {
        "text": "西湖是杭州最著名的景点，被列入世界文化遗产。苏堤春晓、断桥残雪、雷峰夕照等西湖十景闻名遐迩。建议游览时间半天到一天，可以租自行车环湖骑行约15公里。春季（3-5月）和秋季（9-11月）是最佳游览季节。",
        "source": "杭州·西湖",
    },
    {
        "text": "灵隐寺始建于东晋咸和元年(326年)，是杭州最早的佛教名刹。飞来峰石窟造像是中国南方石窟艺术的瑰宝，共有345尊造像。门票：飞来峰45元，灵隐寺30元。建议清晨前往避开人流。",
        "source": "杭州·灵隐寺",
    },
    {
        "text": "杭州美食以杭帮菜为主，代表菜有西湖醋鱼、东坡肉、龙井虾仁、叫化童鸡、宋嫂鱼羹。知味观、楼外楼、外婆家是老字号代表。河坊街和南宋御街是美食街区，适合晚间逛吃。",
        "source": "杭州·美食文化",
    },
    # 北京
    {
        "text": "故宫又称紫禁城，是明清两代皇宫，世界最大的宫殿建筑群。占地72万平方米，有大小宫殿七十多座，房屋九千余间。建议预留至少半天时间，提前在官网预约门票。中轴线从午门到神武门约960米。",
        "source": "北京·故宫",
    },
    {
        "text": "北京胡同文化是老北京的灵魂。南锣鼓巷、烟袋斜街、五道营胡同是最受欢迎的胡同游线路。推荐体验人力三轮车胡同游，可以深入了解四合院文化和老北京人的生活方式。",
        "source": "北京·胡同文化",
    },
    {
        "text": "北京烤鸭是北京最具代表性的美食。全聚德创立于1864年，以挂炉烤鸭闻名；便宜坊始于1416年，擅长焖炉烤鸭。此外，炸酱面、卤煮火烧、豆汁焦圈也是必尝的老北京小吃。",
        "source": "北京·美食文化",
    },
    # 成都
    {
        "text": "成都是中国首批国家历史文化名城，也是联合国教科文组织认定的美食之都。宽窄巷子由三条平行排列的清朝古街道组成，是成都遗留下来的较成规模的清朝古街道。锦里古街毗邻武侯祠，是体验三国文化和成都民俗的好去处。",
        "source": "成都·城市文化",
    },
    {
        "text": "成都大熊猫繁育研究基地位于成华区，是全球最大的大熊猫人工繁育科研机构。建议早上7:30-10:00前往，此时大熊猫最活跃。园区内有成年大熊猫区、幼年大熊猫区和小熊猫区。门票55元。",
        "source": "成都·大熊猫基地",
    },
    {
        "text": "川菜以麻辣鲜香著称，火锅是成都美食名片。推荐：大龙燚火锅、小龙坎火锅。串串香推荐玉林串串。甜水面、担担面、钟水饺、龙抄手是经典小吃。春熙路、建设路是美食聚集地。",
        "source": "成都·美食文化",
    },
    # 西安
    {
        "text": "秦始皇兵马俑是世界第八大奇迹，位于西安临潼区，距市区约40公里。共有三个坑，一号坑最大，面积14260平方米，有陶俑陶马约6000件。建议请景区讲解员，门票120元，游览时间2-3小时。",
        "source": "西安·兵马俑",
    },
    {
        "text": "西安城墙是中国现存最完整的古代城垣建筑，全长13.74公里。强烈推荐租自行车在城墙上骑行，大约需要1.5-2小时。南门（永宁门）是最佳登城点，夜间灯光效果尤为壮观。门票54元。",
        "source": "西安·城墙",
    },
    {
        "text": "西安美食以面食和清真美食为主。回民街是最集中的美食区：老孙家泡馍、贾三灌汤包、红红酸菜炒米都是必吃。biangbiang面、肉夹馍、凉皮被称为'三秦套餐'。永兴坊的摔碗酒也值得体验。",
        "source": "西安·美食文化",
    },
    # 通用攻略
    {
        "text": "旅行预算参考：国内旅游人均每天消费约300-800元（经济型300-500，舒适型500-800）。住宿方面，经济型酒店150-300元/晚，四星级400-800元/晚。景区门票一般50-200元。建议提前在各景区官方公众号预约门票。",
        "source": "通用·预算参考",
    },
    {
        "text": "高铁出行贴士：建议提前1-2周在12306购票。热门线路如京沪、京广、沪杭在节假日一票难求。二等座性价比最高。大件行李可放车厢连接处。提前30分钟到站安检进站。",
        "source": "通用·交通贴士",
    },
]


def main():
    print(f"正在加载 Embedding 模型: {settings.embedding_model} ...")
    embedder = SentenceTransformer(settings.embedding_model)

    persist_dir = settings.chroma_persist_dir
    Path(persist_dir).mkdir(parents=True, exist_ok=True) 

    client = chromadb.PersistentClient(path=persist_dir)
    col = client.get_or_create_collection(
        "tourism_knowledge", metadata={"hnsw:space": "cosine"}
    )

    texts = [d["text"] for d in DOCS]
    print(f"正在编码 {len(texts)} 条知识...")
    embeddings = embedder.encode(texts, normalize_embeddings=True).tolist()

    import hashlib

    ids = [hashlib.md5(t.encode()).hexdigest()[:12] for t in texts]
    metadatas = [{"source": d["source"]} for d in DOCS]

    col.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
    print(f"✅ 已写入 {col.count()} 条知识到 {persist_dir}")


if __name__ == "__main__":
    main()
