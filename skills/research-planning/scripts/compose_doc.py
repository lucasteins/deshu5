# -*- coding: utf-8 -*-
"""按正式格式模板生成综述/需求清单/方向谋划长文档（调用项目 LLM 通道，分节生成后装配）。
用法:
  python skills/research-planning/scripts/compose_doc.py type=survey title=能源算电碳协同前沿综述 topics=算电碳协同,绿色Token
  python skills/research-planning/scripts/compose_doc.py type=requirements title=下阶段科技需求清单
  python skills/research-planning/scripts/compose_doc.py type=planning title=研究方向谋划报告
  python skills/research-planning/scripts/compose_doc.py type=review title=《某需求》需求先进性审查报告 target=kjgl研究谋划/inbox/xxx.md
说明:
  - 文献来源为 skills/research-planning/results/*.json（先用 search_* 脚本检索）；topics 缺省用全部主题。
  - kjgl 业务底数自动读取 kjgl研究谋划/kjgl_base.json（若存在）。
  - 引用纪律：只许引用传入的编号文献表；生成后脚本自动重编号并生成《参考文献》。
"""
import datetime
import glob
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, SCRIPT_DIR)
import lit_common as C  # noqa: E402

RESULTS_DIR = os.path.join(C.SKILL_DIR, 'results')
REF_DIR = os.path.join(C.SKILL_DIR, 'references')
KJGL_BASE = os.path.join(PROJECT_ROOT, 'kjgl研究谋划', 'kjgl_base.json')
OUT_DIR = os.path.join(PROJECT_ROOT, 'kjgl研究谋划', 'output')
TYPE_CODE = {'survey': 'ZS', 'requirements': 'XQ', 'planning': 'MH', 'review': 'SC'}
TYPE_NAME = {'survey': '前沿综述', 'requirements': '需求清单', 'planning': '研究方向谋划', 'review': '审查报告'}

SECTIONS = {
    'review': [
        ('一、审查对象与审查依据', '含（一）审查对象（对照需求原文概述背景、问题定义与研究内容构成，不发挥）（二）审查依据与方法（写明公司项目/需求底数覆盖范围、三路检索通道与主题、检索日期、引用纪律）'),
        ('二、与公司现有科技项目及需求的关系', '含（一）项目库查重结果（逐项比对 kjgl 项目库与规划需求；相关度较高者列"表1 本需求与公司现有相关任务对照表"，列：现有任务|年度|研究侧重|与本需求的关系）（二）衔接关系分析（逐项说明互补/接力/边界划分建议）'),
        ('三、国内外研究前沿对标', '（一）总体态势（问题定义与国际前沿的同向性，带引用[n]）；随后按需求原文的各研究内容逐项各设一节对标（每项含：国际代表性工作带引用[n]、本需求差异化判断、需收窄或注意之处）；末节为行业应用对标（电力行业同类布局与先发窗口判断）'),
        ('四、先进性结论与建议', '含（一）总体结论（明确是否具备先进性、达到何种档位，含"表2 分项先进性评定表"，列：研究内容|国际前沿对应|评定|主要依据）（二）立项完善建议（创新点收窄、边界划分、指标基线、验证方法、合规依据，逐条可操作）'),
    ],
    'survey': [
        ('一、编制说明', '含（一）编制目的与用途（二）综述范围与主题划分（三）数据来源与检索方法（据实写明检索日期、各通道条数）（四）术语与缩略语表'),
        ('二、总体态势判断', '按"并跑且场景领先/并跑需加速/跟随补基座"三档判断各主题相对国际前沿的位置，每档给出依据并带引用'),
        ('三、分主题前沿综述', '逐主题展开，每个主题固定四小节：（一）国际前沿动态（代表性工作5-10项，逐项带引用[n]并注明出处年份）（二）国内研究现状（三）与公司在研项目及规划需求的对照（落到具体项目编号/需求名称）（四）差距分析与机会判断'),
        ('四、主题间协同分析', '指出主题间公共底座与联动关系'),
        ('五、结论与建议', '归纳总体结论，提出下一步工作建议'),
    ],
    'requirements': [
        ('一、编制说明', '含（一）编制依据（二）清单结构说明（演进类R/新拓类N定义）'),
        ('二、需求清单汇总表', '表1：编号|需求名称|类型|技术领域|建议经费(万元)|周期|优先级|衔接项目/需求；先给全量汇总'),
        ('三、演进类需求详述', '每条按模板：1.技术领域 2.类型与衔接 3.问题与必要性（前沿判断带引用[n]）4.研究内容要点 5.预期成果与考核指标（可量化）6.建议经费与周期 7.建议合作单位'),
        ('四、新拓类需求详述', '同上演进类模板'),
        ('五、入库与评审建议', '入库流程、评审要点、需前置准备的论证材料'),
    ],
    'planning': [
        ('一、现状与基础', '（一）在研/待验收/已验收项目梯队（表1）（二）规划需求储备（三）平台与数据资产'),
        ('二、形势分析', '（一）国际技术态势（带引用[n]）（二）政策与行业背景（三）公司战略契合点'),
        ('三、总体思路', '4-6条主线及各主线"延续/深化/新拓"逻辑'),
        ('四、方向矩阵', '表2：主线|方向|类型|对应需求/项目|优先级；须覆盖全部在研项目与规划需求'),
        ('五、优先级与立项建议', 'P0/P1/P2/P3分级，每级给出可核实理由'),
        ('六、路线图', '按半年度列里程碑（表3），里程碑须有产出物名称'),
        ('七、合作与转化布局', '（一）高校/机构合作布局（二）标准与成果转化通道'),
        ('八、风险与保障措施', '技术/进度/依赖/政策四类风险逐条+对策'),
    ],
}


def parse_args(argv):
    args = {}
    for a in argv:
        if '=' in a:
            k, v = a.split('=', 1)
            args[k] = v
    if args.get('type') not in TYPE_CODE:
        print('缺少或不支持 type= 参数，可选: ' + '|'.join(TYPE_CODE))
        sys.exit(1)
    args.setdefault('title', TYPE_NAME[args['type']])
    return args


def load_results(topic_filter):
    papers, topics = [], []
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, '*.json'))):
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        topic = d.get('topic') or os.path.splitext(os.path.basename(path))[0]
        if topic_filter and topic not in topic_filter and C.slugify(topic) not in topic_filter:
            continue
        topics.append(topic)
        papers.extend(d.get('papers', []))
    return papers, topics


def build_ref_list(papers):
    """编号文献表：[n] 作者. 标题[类型]. 出处, 年. 标识."""
    refs, lines = [], []
    for i, p in enumerate(papers, 1):
        au = ', '.join((p.get('authors') or [])[:3]) or '佚名'
        lt = {'arxiv': 'EB', 'ieee': 'C', 'nature': 'J'}.get(p.get('source'), 'EB')
        if p.get('source') == 'ieee' and 'Trans' in (p.get('venue') or ''):
            lt = 'J'
        ident = f"arXiv:{p['id']}" if p.get('source') == 'arxiv' else (f"DOI:{p['doi']}" if p.get('doi') else p.get('url', ''))
        refs.append({'n': i, 'line': f"[{i}] {au}. {p.get('title','')}[{lt}]. {p.get('venue','')}, {p.get('year','?')}. {ident}."})
        lines.append(refs[-1]['line'])
    return refs, lines


def load_kjgl_summary():
    if not os.path.exists(KJGL_BASE):
        return '（kjgl_base.json 不存在，本节约略公司项目/需求明细）'
    with open(KJGL_BASE, encoding='utf-8') as f:
        d = json.load(f)
    lines = ['【公司项目库】']
    for p in d.get('projects', []):
        lines.append(f"- {p.get('project_code') or '无编号'}《{p['name']}》{p['stage']} {p.get('year')}年 经费{p.get('budget_approved')}万 单位:{p.get('org')} 负责人:{p.get('leader')}")
    lines.append('【十五五规划需求】')
    for r in d.get('requirements', []):
        lines.append(f"- 需求{r.get('sort_no') or r['id']}《{r['name']}》 领域:{r.get('tech_field')} 经费:{r.get('total_cost')}万 合作:{r.get('partners')} 负责人:{r.get('leader')}")
    return '\n'.join(lines)


def flatten_subheadings(text):
    # 规范禁止 ### 及以下层级：二级及以下标题统一转为加粗文字
    return re.sub(r'^#{3,}\s*(.+)$', r'**\1**', text, flags=re.M)


def renumber_citations(text, refs):
    used = []
    for m in re.finditer(r'\[(\d{1,3})\]', text):
        n = int(m.group(1))
        if 1 <= n <= len(refs) and n not in used:
            used.append(n)
    mapping = {old: i + 1 for i, old in enumerate(used)}

    def repl(m):
        n = int(m.group(1))
        return f'[{mapping[n]}]' if n in mapping else ''
    text = re.sub(r'\[(\d{1,3})\]', repl, text)
    bib = [refs[old - 1]['line'].replace(f'[{old}]', f'[{mapping[old]}]', 1) for old in used]
    return text, bib


def main():
    args = parse_args(sys.argv[1:])
    topic_filter = [t for t in args.get('topics', '').split(',') if t] or None
    papers, topics = load_results(topic_filter)
    if not papers:
        print('检索结果库为空：请先运行 search_arxiv/search_ieee/search_nature 检索文献。')
        sys.exit(2)
    refs, ref_lines = build_ref_list(papers)
    kjgl = load_kjgl_summary()

    # review 类型必读审查对象原文（target= 项目相对路径，先经 tools/save_text.py 暂存）
    target_text = ''
    if args['type'] == 'review':
        target_rel = args.get('target', '').replace('\\', '/')
        target_path = os.path.abspath(os.path.join(PROJECT_ROOT, target_rel)) if target_rel else ''
        if (not target_rel or '..' in target_rel
                or not target_path.startswith(os.path.abspath(PROJECT_ROOT) + os.sep)
                or not os.path.isfile(target_path)):
            print('type=review 需要 target=<项目相对路径> 指向审查对象原文'
                  '（先用 python tools/save_text.py <文件名.md> <内容> 暂存到 kjgl研究谋划/inbox/）')
            sys.exit(1)
        with open(target_path, encoding='utf-8', errors='replace') as f:
            target_text = f.read()[:12000]
    target_block = f'\n【审查对象需求原文】\n{target_text}\n' if target_text else ''

    with open(os.path.join(REF_DIR, 'format_common.md'), encoding='utf-8') as f:
        fmt_common = f.read()
    with open(os.path.join(REF_DIR, f"format_{args['type']}.md"), encoding='utf-8') as f:
        fmt_type = f.read()

    from core.llm_transport import call_llm
    today = datetime.date.today().strftime('%Y年%m月%d日')
    stats = f'检索库共 {len(papers)} 篇，主题：{"、".join(topics)}；生成日期 {today}'
    print(f'[compose] type={args["type"]} 文献={len(papers)} 主题数={len(topics)}', flush=True)

    body_parts = []
    for title, spec in SECTIONS[args['type']]:
        prompt = f"""你是电力企业科技管理文书专家。请按正式格式撰写《{args['title']}》的第"{title}"部分。

【通用格式规范】
{fmt_common}

【本文档类型模板（节结构与质量要求）】
{fmt_type}

【检索库概况】{stats}

【公司项目与需求底数】
{kjgl}
{target_block}
【可用文献编号表】（引用只许用 [n] 形式且 n 必须在下表范围内；每条文献按需引用，不得编造表外文献）
{chr(10).join(ref_lines)}

【本节任务】撰写"{title}"：{spec}。
要求：直接输出该节正文（一级标题用"## {title}"起；文档头、文档编号、密级、编制单位、参考文献表、版本记录均由脚本统一装配，禁止在正文中重复输出）。
"""
        print(f'[compose] 正在生成「{title}」（LLM 调用中，最长约 10 分钟）…', flush=True)
        r = call_llm(prompt, max_tokens=8000, thinking=False, timeout=600)
        content = (r.get('content') or '').strip()
        print(f'[compose] {title} 完成，{len(content)} 字', flush=True)
        body_parts.append(content)

    body = flatten_subheadings('\n\n'.join(body_parts))
    body, bib = renumber_citations(body, refs)

    seq = len(glob.glob(os.path.join(OUT_DIR, '*.md'))) + 1
    code = f"KJGL-{TYPE_CODE[args['type']]}-{datetime.date.today().year}-{seq:03d}"
    month = datetime.date.today().strftime('%Y年%m月')  # 通用规范：编制日期精确到月
    header = (f"文档编号：{code}\n密级：内部\n"
              f"编制单位：国网浙江省电力有限公司信息通信分公司 能源大数据服务中心\n"
              f"编制日期：{month}\n版本：V1.0\n\n---\n\n")
    tail = ('\n\n## 参考文献\n\n' + ('\n'.join(bib) if bib else '（本文未引用文献）') +
            f"\n\n---\n附：版本记录\n\n| 版本 | 日期 | 编制/修订说明 | 审核 |\n|---|---|---|---|\n"
            f"| V1.0 | {datetime.date.today().isoformat()} | 初稿（AI 辅助生成，待人工审核） | （待审） |\n")
    if not re.match(r'^#\s', body.lstrip()):  # 缺一级文档标题才补（## 一级章不算）
        body = f"# {args['title']}\n\n" + body
    doc = header + body + tail

    os.makedirs(OUT_DIR, exist_ok=True)
    fname = f"{code}-{C.slugify(args['title'])}.md"
    path = os.path.join(OUT_DIR, fname)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(doc)

    print('\n[SUMMARY]')
    print(f'文档类型: {TYPE_NAME[args["type"]]} 编号: {code}')
    print(f'引用文献: {len(bib)} 条（检索库 {len(papers)} 篇）')
    print(f'文档路径: {os.path.relpath(path, PROJECT_ROOT)}')
    print(f'全文字数: {len(doc)}')


if __name__ == '__main__':
    main()
