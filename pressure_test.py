# -*- coding: utf-8 -*-
import random
import time
import signal
import os
import datetime
import hmac
import hashlib
import gevent
import string
import sys
import traceback
import threading
from gevent.queue import (Queue, Empty)
from dock_instame2.server import app as ap
from dock_instame2 import log
from dock.protocols.websocket.client import WebSocketClient
from dock_keepserver.client import KeepClient

_account_list = []  # 保存用户id，测试最后删除

hosts = ["0.0.0.0", "192.168.1.1"]
ws_port = 8014
wss_port = 8443
keep_proxy_port = 6000
keep_client_port = 7290
sig_kv = 1
sig_key = ''
cond = threading.Condition()
ACCOUNT_NUMBER = 50000

# 调试参数
account_sum = 10000  # 生成的用户数量
threading_count = int(account_sum / 100)  # 进程数量 进程数需要能被用户数量整除  40
coroutineNum = int(account_sum / threading_count)  # 每个进程的携程数量 100
account_coroutineNum = 20  # 生成用户账号的携程数量
account_coroutineLoop = int(coroutineNum / account_coroutineNum)  # 每个携程要生成多少个用户 5

# 常量
start_moment = time.time()
sleep_time = 25  # 每次notify间隔时间
timeout = 60  # keepclient 超时时间
msg_count = 0  # 总请求数字
success_resp = 0  # 成功响应数
failed_resp = 0  # 失败响应数
GT3 = 0  # 超过3s
LT3 = 0  # 小于3s
MAXTIME = 0  # 最大响应时间
MINTIME = 0  # 最小响应时间
client_count = 0  # websocket 建立的链接总数
failed_cilent_count = 0  # 失败总数
ws_connect_failed_count = 0  # websocket链接建立异常
kp_connect_failed_count = 0  # keepserver链接建立异常
except_failed_count = 0  # kp链接建立失败
notify_failed_count = 0  # 唤醒失败，建立后再次通信失败
account_count = 0  # 生成的用户计数
instame_count = 0  # 链接在instame上的websocket
bestdate_count = 0  # 链接在bestdate上的websocket
stop_generate = False
end_of_line = "\n"


def _timebase60(d):
    base_60 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz23456789'
    millisecond = d.microsecond / 1000
    return '%s%s%s%s%s%s%s%s' % (
        base_60[d.year % 60], base_60[d.month - 1], base_60[d.day - 1], base_60[d.hour],
        base_60[d.minute], base_60[d.second], base_60[millisecond / 60], base_60[millisecond % 60])


class Account(object):

    def __init__(self, ):
        global ACCOUNT_NUMBER
        self.app_id = 101
        self.mid = ACCOUNT_NUMBER
        time_str = _timebase60(datetime.datetime.now())
        rand_str = ''.join([random.choice(string.digits + string.ascii_lowercase) for _ in range(32)])
        self.session_id = '%s%s' % (time_str, rand_str)
        ACCOUNT_NUMBER += 1


class PressureTestCase(object):

    def __init__(self):
        ap.flaskapp.config["TESTING"] = True
        self.app = ap.flaskapp.test_client()

    def connect(self, account):

        global hosts
        host = random.choice(hosts)
        signed_str = "%s.%s/%s" % (account.mid, account.app_id, account.session_id)
        signature = hmac.new(sig_key, signed_str, hashlib.sha256).hexdigest()
        ws_str = 'ws://%s:%s/%s.%s/%s?sig_kv=%s&signature=%s' % (
            host, ws_port, account.mid, account.app_id, account.session_id, sig_kv, signature)
        ws = WebSocketClient(ws_str)
        ws.connect()
        log.info("connect to ws")
        client = KeepClient(host=host, port=keep_proxy_port, timeout=60)
        print "connect to kp"
        print client
        client.notify(account.app_id, account.mid, {"hello": "world"})
        return client

    def new_account(self, ):
        account = Account()
        _account_list.append(account)
        return account

    def remove_account(self, account):
        if not account:
            return
        if account.device_manager:
            account.device_manager.delete()
        for sub in account.sub_accounts():
            if sub:
                sub.delete(force=True)
        if account.main_account:
            account.main_account.delete(force=True)

    def maxtime(self, timestamp):
        global MAXTIME
        if timestamp > MAXTIME:
            MAXTIME = timestamp

    def mintime(self, timestamp):
        global MINTIME
        if timestamp < MINTIME:
            MINTIME = timestamp


class TestThreads(threading.Thread):

    def __init__(self, name):
        global coroutineNum
        global account_coroutineNum
        global account_coroutineLoop
        global account_sum
        threading.Thread.__init__(self)
        self.name = name
        self.coroutineNum = coroutineNum  # 生成connection的worker数，每个线程建立多少websocket链接设置多少
        self.generate_connection_queue = Queue(maxsize=self.coroutineNum)  # 保存生成账户的队列
        self.thread_count = 0
        self.account_coroutineNum = account_coroutineNum
        self.account_coroutineLoop = account_coroutineLoop
        self.session_file = "sesion_tmp_file{}.tmp".format(account_sum)

    def run(self):
        threads = [gevent.spawn(self.generate_accounts_boss) for _ in xrange(self.account_coroutineNum)] + [
            gevent.spawn(self.generate_connection_worker) for _ in
            xrange(self.coroutineNum)]
        gevent.joinall(threads)

    def print_msg(self):
        global client_count
        global failed_cilent_count
        global ws_connect_failed_count
        global kp_connect_failed_count
        global except_failed_count
        global notify_failed_count
        global instame_count
        global bestdate_count
        log.info(
            "client suspect sum is {},failed sum is {},ws except failed is {},kp except is {},kp connect failed is {},notify failed is {},instame_large is {},instame is {}".format(
                client_count, failed_cilent_count, ws_connect_failed_count, kp_connect_failed_count,
                except_failed_count, notify_failed_count, instame_count, bestdate_count))

    def generate_accounts_boss(self):
        generator = PressureTestCase()
        global account_count
        global account_coroutineLoop
        global start_moment
        for _ in xrange(self.account_coroutineLoop):
            # print "boss generate account {}".format(_)
            acc = generator.new_account()
            account_count += 1
            self.generate_connection_queue.put(acc)
            now = time.time()
            log.info("now account is {},cost {} seconds".format(account_count, now - start_moment))

    def generate_connection_worker(self):
        global hosts
        # 连接统计
        global client_count
        global failed_cilent_count
        global instame_count
        global bestdate_count

        # 异常计数
        global ws_connect_failed_count
        global kp_connect_failed_count
        global except_failed_count
        global notify_failed_count

        while not self.generate_connection_queue.empty():
            tmp_sec = random.random() * 10
            gevent.sleep(tmp_sec)
            host = random.choice(hosts)
            account = self.generate_connection_queue.get()
            signed_str = "%s.%s/%s" % (account.mid, account.app_id, account.session_id)
            signature = hmac.new(sig_key, signed_str, hashlib.sha256).hexdigest()
            ws_str = 'ws://%s:%s/%s.%s/%s?sig_kv=%s&signature=%s' % (
                host, ws_port, account.mid, account.app_id, account.session_id, sig_kv, signature)
            kp_client = None
            client_count += 1

            try:
                ws = WebSocketClient(ws_str)
                ws.connect()
            except Exception, e:
                # ws 异常
                ws_connect_failed_count += 1
                failed_cilent_count += 1
                print e
                print traceback.format_exc()
                self.print_msg()
                break

            try:
                kp_client = KeepClient(host=host, port=keep_proxy_port, timeout=timeout)
            except Exception, e:
                # kp 异常
                kp_connect_failed_count += 1
                failed_cilent_count += 1
                print e
                print traceback.format_exc()
                self.print_msg()
                break

            if kp_client:
                self.thread_count += 1
                if host == "0.0.0.0":
                    instame_count += 1
                else:
                    bestdate_count += 1
                self.print_msg()
            else:
                except_failed_count += 1
                failed_cilent_count += 1
                self.print_msg()
            if self.thread_count >= self.coroutineNum:
                log.info("{}'s account all connected!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!".format(self.name))
            while True:
                try:
                    kp_client.notify(account.app_id, account.mid, {"test": "hello world,{}".format(account.mid)})
                    self.print_msg()
                    gevent.sleep(sleep_time)
                except Exception, e:
                    notify_failed_count += 1
                    failed_cilent_count += 1
                    self.print_msg()
                    print e
                    print traceback.format_exc()


def single_test():
    test = PressureTestCase()
    acc = test.new_account()
    test.connect(acc)


def asy_job():
    # signal.signal(signal.SIGINT, signal_handler)  # 注册信号处理函数
    # signal.signal(signal.SIGTERM, signal_handler)  # 注册信号处理函数
    threads = []
    for i in xrange(threading_count):
        threads.append(TestThreads("thread {}".format(i)))
    for thread in threads:
        # thread.setDaemon(True)
        thread.start()
    for thread in threads:
        thread.join()


def signal_handler(signum, frame):  # 信号处理函数
    global is_exit
    is_exit = True  # 主线程信号处理函数修改全局变量，提示子线程退出
    log.info("Get signal, set is_exit = True")


if __name__ == "__main__":
    # TODO:设置信号处理，可以用control + c 关闭进程并报告运行结果
    log.info("start testing")
    start = time.time()
    log.info("this time will generate {} accounts".format(account_sum))
    # single_test()
    asy_job()
