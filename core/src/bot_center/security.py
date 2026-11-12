"""Single-use launcher tickets and restart-scoped HTTP sessions."""
import secrets,threading,time
class Sessions:
    def __init__(self):self.lock=threading.Lock();self.tickets={};self.sessions={}
    def ticket(self):
        with self.lock:
            now=time.monotonic();self.tickets={key:expiry for key,expiry in self.tickets.items() if expiry>now}
            token=secrets.token_urlsafe(32);self.tickets[token]=now+60;return token
    def exchange(self,ticket):
        with self.lock:
            expiry=self.tickets.pop(ticket,0)
            if expiry<time.monotonic():return None
            identity=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            self.sessions[identity]={'csrf':csrf,'expires':time.monotonic()+12*3600};return identity,csrf
    def session(self,identity):
        with self.lock:
            session=self.sessions.get(identity)
            if session and session['expires']>time.monotonic():return session
            self.sessions.pop(identity,None);return None
