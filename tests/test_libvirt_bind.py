"""Tests for the Libvirt bind's cloud-init volume upload handling."""

import sys
import threading
from types import SimpleNamespace

import pytest

from broker.binds.libvirt import LibvirtBind


class FakeISO:
    def new(self, **kwargs):
        pass

    def add_fp(self, *args, **kwargs):
        pass

    def write_fp(self, stream):
        stream.write(b"complete-cloud-init-iso")

    def close(self):
        pass


class FakeStream:
    def __init__(self, fail=False):
        self.fail = fail
        self.data = bytearray()
        self.aborted = False
        self.finished = False

    def send(self, chunk):
        if self.fail:
            raise RuntimeError("upload failed")
        accepted = min(3, len(chunk))
        self.data.extend(chunk[:accepted])
        return accepted

    def abort(self):
        self.aborted = True

    def finish(self):
        self.finished = True


class FakeVolume:
    def __init__(self):
        self.deleted = False

    def upload(self, stream, offset, length, flags):
        pass

    def delete(self, flags=0):
        self.deleted = True


def make_bind(stream):
    volume = FakeVolume()
    pool = SimpleNamespace(createXML=lambda xml, flags: volume)
    connection = SimpleNamespace(
        isAlive=lambda: True,
        storagePoolLookupByName=lambda name: pool,
        newStream=lambda flags: stream,
    )
    bind = LibvirtBind.__new__(LibvirtBind)
    bind._local = threading.local()
    bind._local.conn = connection
    bind.storage_pool_name = "default"
    return bind, volume


def test_cloud_init_upload_retries_short_writes(monkeypatch):
    monkeypatch.setitem(sys.modules, "pycdlib", SimpleNamespace(PyCdlib=FakeISO))
    stream = FakeStream()
    bind, volume = make_bind(stream)

    result = bind.create_cloud_init_iso("seed.iso", "meta", "user")

    assert result is volume
    assert bytes(stream.data) == b"complete-cloud-init-iso"
    assert stream.finished
    assert not volume.deleted


def test_cloud_init_upload_aborts_and_deletes_on_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "pycdlib", SimpleNamespace(PyCdlib=FakeISO))
    stream = FakeStream(fail=True)
    bind, volume = make_bind(stream)

    with pytest.raises(RuntimeError, match="upload failed"):
        bind.create_cloud_init_iso("seed.iso", "meta", "user")

    assert stream.aborted
    assert volume.deleted
