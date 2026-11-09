import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('packager', pathlib.Path(__file__).with_name('package_updater.py'))
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.image = self.root / 'image.tar'
        self.create_image()

    def create_image(self, architecture='amd64', revision='a' * 40):
        config = json.dumps(dict(architecture=architecture, os='linux', rootfs=dict(type='layers', diff_ids=[]),
                                 config=dict(Labels={'org.opencontainers.image.revision': revision}))).encode()
        self.image_id = 'sha256:' + hashlib.sha256(config).hexdigest()
        with tarfile.open(self.image, 'w') as archive:
            for name, value in [('config.json', config), ('manifest.json', json.dumps([{'Config': 'config.json', 'RepoTags': ['cantabile:fixture'], 'Layers': []}]).encode())]:
                info = tarfile.TarInfo(name)
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))

    def test_catalog_preserves_app_data_and_contains_digested_payload(self):
        out = self.root / 'out'
        recipe = packager.package(self.image, self.image_id, 'a' * 40, out)
        catalog = json.loads((out / 'catalog.json').read_text())
        self.assertEqual(catalog['recipes'], [recipe])
        self.assertEqual(recipe['platform'], 'linux/amd64')
        app = recipe['application']
        self.assertEqual(app['kind'], 'docker')
        self.assertEqual(app['image_id'], self.image_id)
        self.assertEqual(app['network'], 'host')
        self.assertEqual(app['mounts'], [{'source': '${root}/data', 'target': '/data', 'read_only': False}, {'source': '${SOUNDFONT_ROOT}', 'target': '/soundfonts', 'read_only': True}])
        self.assertTrue(recipe['settings'][2]['secret'])
        self.assertTrue(app['files'][0]['source']['private'])
        actual = 'sha256:' + hashlib.sha256((out / 'linux-amd64--image.tar').read_bytes()).hexdigest()
        self.assertEqual(recipe['downloads']['image.tar']['digest'], actual)
        self.assertEqual(app['image_archive']['digest'], actual)
        self.assertEqual(json.loads((out / 'candidate.json').read_text())['source_revision'], 'a' * 40)

    def test_wrong_image_id_cannot_publish_catalog(self):
        with self.assertRaises(ValueError):
            packager.package(self.image, 'sha256:' + '0' * 64, 'a' * 40, self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

    def test_existing_output_is_not_overwritten(self):
        out = self.root / 'out'
        out.mkdir()
        (out / 'retained').write_text('keep')
        with self.assertRaises(ValueError):
            packager.package(self.image, self.image_id, 'a' * 40, out)
        self.assertEqual((out / 'retained').read_text(), 'keep')

    def test_symlink_input_rejected(self):
        link = self.root / 'link.tar'
        link.symlink_to(self.image)
        with self.assertRaises(ValueError):
            packager.package(link, self.image_id, 'a' * 40, self.root / 'out')

    def test_wrong_platform_is_not_labeled_amd64(self):
        self.create_image(architecture='arm64')
        with self.assertRaises(ValueError):
            packager.package(self.image, self.image_id, 'a' * 40, self.root / 'out')

    def test_revision_must_match_the_actual_image_label(self):
        with self.assertRaises(ValueError):
            packager.package(self.image, self.image_id, 'b' * 40, self.root / 'out')


if __name__ == '__main__':
    unittest.main()
