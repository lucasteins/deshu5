# -*- coding: utf-8 -*-
"""打印文档格式模板。用法:
python skills/research-planning/scripts/show_template.py common|survey|requirements|planning|review
"""
import os
import sys

REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'references')
MAP = {'common': 'format_common.md', 'survey': 'format_survey.md',
       'requirements': 'format_requirements.md', 'planning': 'format_planning.md',
       'review': 'format_review.md'}

if len(sys.argv) < 2 or sys.argv[1] not in MAP:
    print('用法: show_template.py ' + '|'.join(MAP))
    sys.exit(1)
with open(os.path.join(REF, MAP[sys.argv[1]]), encoding='utf-8') as f:
    print(f.read())
