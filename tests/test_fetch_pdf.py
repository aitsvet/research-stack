import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'fetch_pdf.sh'

class FreshPDF(unittest.TestCase):
    def run_render(self, fail):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'result.pdf'
            output.write_bytes(b'old PDF')
            docker = root / 'docker'
            docker.write_text('#!/bin/bash\n'
                'if [ "$1" = cp ]; then\n'
                '  [ "$FAIL_STAGE" != copy ] || exit 8\n'
                '  printf "new PDF" > "$3"\n'
                'elif [ "$FAIL_STAGE" = render ]; then exit 9; fi\n')
            docker.chmod(0o700)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                       FAIL_STAGE=fail, FETCH_PDF_NO_MD='1')
            result = subprocess.run(['bash', str(SCRIPT), 'http://example.invalid',
                                     str(root), 'result'], env=env, capture_output=True)
            return result.returncode, output.read_bytes(), list(root.glob('.render.*'))

    def test_failed_render_preserves_old_pdf_and_fails(self):
        code, body, staged = self.run_render('render')
        self.assertNotEqual(code, 0)
        self.assertEqual(body, b'old PDF')
        self.assertEqual(staged, [])

    def test_failed_copy_preserves_old_pdf_and_fails(self):
        code, body, staged = self.run_render('copy')
        self.assertNotEqual(code, 0)
        self.assertEqual(body, b'old PDF')
        self.assertEqual(staged, [])

    def test_fresh_render_replaces_destination(self):
        code, body, staged = self.run_render('')
        self.assertEqual(code, 0)
        self.assertEqual(body, b'new PDF')
        self.assertEqual(staged, [])

if __name__ == '__main__':
    unittest.main()
