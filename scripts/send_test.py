#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests, json, sys
r = requests.post(
    'http://127.0.0.1:8848/api/v1/groups/g_878b8bbd4747/send',
    json={
        'text': '你好，今天天气怎么样？',
        'by': 'user',
        'to': ['@foreman'],
        'path': '',
        'priority': 'normal',
    }
)
print(r.status_code, r.text[:200])
