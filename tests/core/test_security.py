import json,tempfile,time,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from bot_center.api import Core,create_app
from bot_center.security import Sessions

class Security(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);(root/'config').mkdir()
        (root/'config/registry.json').write_text('{"schema":1,"bots":[]}',encoding='utf-8')
        self.core=Core(root,port=8765);self.client=TestClient(create_app(self.core,start_tasks=False),base_url=self.core.origin)
    def tearDown(self):self.client.close();self.core.commands.close();self.temp.cleanup()
    def authenticate(self):
        ticket=self.core.sessions.ticket();response=self.client.post('/api/session',json={'ticket':ticket},headers={'Origin':self.core.origin});self.assertEqual(200,response.status_code);return response.json()['csrf'],ticket
    def test_private_read_apis_all_require_session(self):
        for path in ['/api/status','/api/schedules','/api/backups','/api/logs/home']:
            self.assertEqual(401,self.client.get(path).status_code)
    def test_ticket_single_use_and_cookie_flags(self):
        ticket=self.core.sessions.ticket();response=self.client.post('/api/session',json={'ticket':ticket},headers={'Origin':self.core.origin})
        self.assertEqual(200,response.status_code);cookie=response.headers['set-cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=strict',cookie)
        self.assertEqual(401,self.client.post('/api/session',json={'ticket':ticket},headers={'Origin':self.core.origin}).status_code)
    def test_host_origin_and_csrf_checked(self):
        csrf,_=self.authenticate()
        self.assertEqual(403,self.client.get('/api/status',headers={'Host':'evil.local'}).status_code)
        self.assertEqual(403,self.client.post('/api/commands',json={'action':'quit'},headers={'Origin':'http://evil.local','X-CSRF-Token':csrf}).status_code)
        self.assertEqual(403,self.client.post('/api/commands',json={'action':'quit'},headers={'Origin':self.core.origin}).status_code)
        self.assertFalse(self.core.commands.launch_barrier)
        response=self.client.post('/api/commands',json={'action':'quit'},headers={'Origin':self.core.origin,'X-CSRF-Token':csrf})
        self.assertEqual('Accepted',response.json()['status']);self.assertTrue(self.core.commands.launch_barrier)
    def test_restart_scoped_sessions_and_expired_tickets(self):
        sessions=Sessions();ticket=sessions.ticket();sessions.tickets[ticket]=0;self.assertIsNone(sessions.exchange(ticket))
        self.authenticate();self.core.sessions=Sessions();self.assertEqual(401,self.client.get('/api/status').status_code)
    def test_diagnostics_rendered_as_text_and_bundled_assets(self):
        self.authenticate()
        response=self.client.get('/');self.assertEqual(200,response.status_code);self.assertIn("script-src 'self'",response.headers['content-security-policy'])
        self.assertIn('textContent',self.client.get('/assets/app.js').text);self.assertNotIn('innerHTML',self.client.get('/assets/app.js').text)
        self.assertEqual(404,self.client.get('/assets/private.py').status_code)
    def test_ticket_rejected_from_foreign_origin_without_consumption(self):
        ticket=self.core.sessions.ticket();self.assertEqual(403,self.client.post('/api/session',json={'ticket':ticket},headers={'Origin':'http://foreign.local'}).status_code)
        self.assertEqual(200,self.client.post('/api/session',json={'ticket':ticket},headers={'Origin':self.core.origin}).status_code)

if __name__=='__main__':unittest.main()
