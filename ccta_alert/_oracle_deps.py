"""Khai báo các module chuẩn của Python mà thư viện oracledb dùng tới.

oracledb (chế độ thin) gọi các module này từ bên trong mã đã biên dịch (thin_impl.pyd), nên PyInstaller
không tự thấy và không đóng gói chúng vào CctaAlert.exe. Ví dụ thiếu `getpass` thì exe báo lỗi
"No module named 'getpass'" khi kết nối HIS. Import ở đây để PyInstaller nhìn thấy và đóng gói đủ.

Danh sách lấy bằng cách cho oracledb import và kết nối thử, rồi ghi lại các module chuẩn đã được nạp,
cộng thêm các module dùng ở bước xác thực / mã hóa. Không xóa file này.
"""
# flake8: noqa
import abc, array, ast, asyncio, atexit, base64, binascii, bisect, codecs, codeop, collections, contextlib
import contextvars, copy, copyreg, dataclasses, datetime, decimal, dis, email, enum, errno, functools
import genericpath, getpass, hashlib, heapq, hmac, inspect, io, ipaddress, itertools, json, keyword, linecache
import locale, logging, math, ntpath, numbers, opcode, operator, os, platform, random, re, reprlib, secrets
import select, selectors, signal, socket, ssl, stat, string, stringprep, struct, subprocess, textwrap
import threading, time, token, tokenize, traceback, types, typing, unicodedata, urllib, uuid, warnings
import weakref, zlib
import collections.abc, concurrent.futures, email.utils, encodings.idna, urllib.parse
