"""Overall polling deadline remains effective while socket data trickles."""
import threading,time,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from home_bot.bot import Telegram,LOCAL_TEST_TOKEN
from home_bot.managed import single_poll,PollDeadlineExceeded

class PollDeadlineTests(unittest.TestCase):
    def test_actual_urllib_trickle_cannot_extend_overall_deadline(self):
        release=threading.Event();requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                requests.append(self.path)
                self.send_response(200);self.end_headers()
                try:
                    while not release.wait(.02):self.wfile.write(b' ');self.wfile.flush()
                    self.wfile.write(b'{"ok":true,"result":[]}')
                except OSError:pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        api=Telegram(LOCAL_TEST_TOKEN,api_base='http://127.0.0.1:'+str(server.server_port))
        start=time.monotonic()
        try:
            with self.assertRaises(PollDeadlineExceeded):single_poll(lambda:api.call('getUpdates',{'timeout':5},timeout=.1),threading.Event(),deadline_seconds=.25)
            self.assertLess(time.monotonic()-start,1)
            self.assertEqual(len(requests),1)
        finally:release.set();server.shutdown();server.server_close();thread.join(2)
    def test_stop_interrupts_a_pending_poll_without_starting_a_second_one(self):
        stop=threading.Event();release=threading.Event();calls=[]
        def poll():calls.append(1);release.wait(2);return []
        timer=threading.Timer(.05,stop.set);timer.start()
        try:
            with self.assertRaises(InterruptedError):single_poll(poll,stop)
            self.assertEqual(calls,[1])
        finally:release.set();timer.join()

if __name__=='__main__':unittest.main()
