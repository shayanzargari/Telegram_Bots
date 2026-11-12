"""Generate an MP3 in the app's isolated Python environment."""
import asyncio
import json
import ssl
import sys
import urllib.request
from pathlib import Path

import edge_tts
import edge_tts.communicate
from edge_tts.exceptions import NoAudioReceived


def configure():
    # Add the Windows certificate store, including the configured corporate CA.
    # Keep certificate and hostname verification enabled.
    context = edge_tts.communicate._SSL_CTX
    context.load_default_certs()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


async def generate(request, output):
    # aiohttp does not apply HTTPS_PROXY automatically to a wss:// request.
    proxies = urllib.request.getproxies()
    proxy = proxies.get('wss') or proxies.get('https') or proxies.get('http')
    async with asyncio.timeout(48):
        for attempt in range(2):
            try:
                await edge_tts.Communicate(
                    request['text'], request['voice'], rate=request['rate'], proxy=proxy,
                    connect_timeout=8, receive_timeout=20).save(str(output))
                return
            except NoAudioReceived:
                if attempt:
                    raise
                await asyncio.sleep(.25)


if __name__ == '__main__':
    try:
        # The app launcher has no UTF-8 console settings. Pipe encodings must
        # be explicit, just as they are on the parent side of the connection.
        sys.stdin.reconfigure(encoding='utf-8', errors='strict')
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
        configure()
        if sys.argv[1:] == ['--serve']:
            # Keep the imports and certificate setup warm between phrases.
            loop = asyncio.new_event_loop()
            try:
                for line in sys.stdin:
                    try:
                        request = json.loads(line)
                        loop.run_until_complete(generate(request, Path(request['output'])))
                        result = {'ok': True}
                    except Exception as error:
                        result = {'ok': False, 'error': type(error).__name__}
                    print(json.dumps(result), flush=True)
            finally:
                loop.close()
        else:
            request = json.loads(Path(sys.argv[1]).read_text('utf-8'))
            asyncio.run(generate(request, Path(sys.argv[2])))
    except Exception as error:
        # Do not log service URLs, proxy credentials, or the spoken text.
        print(type(error).__name__, file=sys.stderr)
        sys.exit(1)
