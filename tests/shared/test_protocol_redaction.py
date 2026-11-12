import io
import json
import logging
import struct
import unittest

from bot_center_runtime.protocol import read_frame,write_frame,ProtocolError,MAX_FRAME
from bot_center_runtime.redaction import redact,RedactFilter


def canary():return '123456789'+':'+'fixture_canary_'+'A'*35


class ProtocolTests(unittest.TestCase):
    def test_roundtrip_nested_utf8_and_eof(self):
        stream=io.BytesIO();body={'contract_version':1,'kind':'progress','message':'Français فارسی','nested':{'percent':42}}
        write_frame(stream,body);stream.seek(0)
        self.assertEqual(read_frame(stream),body);self.assertIsNone(read_frame(stream))

    def test_oversized_frame_rejected_before_body_is_read_or_written(self):
        stream=io.BytesIO(struct.pack('<I',MAX_FRAME+1))
        with self.assertRaises(ProtocolError):read_frame(stream)
        output=io.BytesIO()
        with self.assertRaises(ProtocolError):write_frame(output,{'value':'X'*(MAX_FRAME+1)})
        self.assertEqual(output.getvalue(),b'')

    def test_truncated_and_malformed_frame_errors_never_echo_payload(self):
        secret=canary()
        for raw in (b'\x03',struct.pack('<I',8)+b'ab',struct.pack('<I',len(secret))+secret.encode(),struct.pack('<I',2)+b'[]'):
            with self.subTest(length=len(raw)),self.assertRaises(ProtocolError) as caught:read_frame(io.BytesIO(raw))
            self.assertFalse(secret in str(caught.exception),'Protocol diagnostics exposed a canary.')


class RedactionTests(unittest.TestCase):
    def test_token_in_canonical_telegram_url_and_plaintext_are_redacted(self):
        secret=canary()
        for value in (secret,'https://api.telegram.org/bot'+secret+'/getUpdates','credential='+secret,{'url':'/bot'+secret}):
            with self.subTest(kind=type(value).__name__):self.assertFalse(secret in redact(value),'Token canary leaked through a diagnostic URL/value.')

    def test_explicit_short_secret_and_logging_exception_are_redacted(self):
        self.assertEqual(redact('value fixture-short',secrets=['fixture-short']),'value [REDACTED]')
        secret=canary();stream=io.StringIO();handler=logging.StreamHandler(stream);handler.addFilter(RedactFilter())
        logger=logging.getLogger('fixture.redaction');logger.handlers=[handler];logger.propagate=False;logger.setLevel(logging.INFO)
        try:
            raise RuntimeError('https://api.telegram.org/bot'+secret+'/getMe')
        except RuntimeError:logger.exception('Request failed at %s','/bot'+secret)
        self.assertFalse(secret in stream.getvalue(),'A logging exception leaked a synthetic token.')


if __name__=='__main__':unittest.main()
