import asyncio
import socket

from engineer.ingest.listener import UdpListener
from tests.synth import PacketFactory


def test_listener_stamps_fans_out_and_forwards():
    async def run():
        fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        fwd.bind(("127.0.0.1", 0))
        fwd.settimeout(2)
        fwd_port = fwd.getsockname()[1]

        listener = UdpListener("127.0.0.1", 0, forward=[("127.0.0.1", fwd_port)])
        got_a, got_b = [], []
        listener.subscribe(lambda raw, t: got_a.append((raw, t)))

        def broken(raw, t):
            raise RuntimeError("boom")

        listener.subscribe(broken)  # must not stop later subscribers
        listener.subscribe(lambda raw, t: got_b.append(raw))
        await listener.start()

        raw = PacketFactory().session()
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(raw, ("127.0.0.1", listener.bound_port))
        for _ in range(100):
            if got_a:
                break
            await asyncio.sleep(0.01)
        forwarded = await asyncio.get_running_loop().run_in_executor(None, fwd.recv, 2048)
        listener.close()
        tx.close()
        fwd.close()
        return listener, got_a, got_b, raw, forwarded

    listener, got_a, got_b, raw, forwarded = asyncio.run(run())
    assert got_a[0][0] == raw
    assert got_a[0][1] > 1_600_000_000 * 10**9  # a real time.time_ns() stamp
    assert got_b == [raw]
    assert forwarded == raw
    assert listener.subscriber_errors == 1
