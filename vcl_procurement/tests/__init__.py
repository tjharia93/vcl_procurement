import sys
import types

# Tests run without a bench. The fake writes the QR's text content as its "SVG", so a test can
# decode a label's QR and check exactly what it holds. A real pyqrcode, if present, is kept.
if 'pyqrcode' not in sys.modules:
    try:
        import pyqrcode  # noqa: F401
    except ImportError:
        class _FakeQR:
            def __init__(self, content, **kw):
                self.content = content

            def svg(self, stream, **kw):
                stream.write(str(self.content).encode())

        sys.modules['pyqrcode'] = types.SimpleNamespace(create=lambda content, **kw: _FakeQR(content, **kw))
