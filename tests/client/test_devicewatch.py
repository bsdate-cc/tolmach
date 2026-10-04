"""The device list is rebuilt when Windows says the devices changed, not on a timer."""
import threading
import time

from tolmach.client.devicewatch import DeviceWatch


class Subscription:
    """Stands in for the Windows subscription: remembers the notify callback and the calls."""

    def __init__(self, fail=False):
        self.fail = fail
        self.notify = None
        self.threads = []
        self.unsubscribed = 0

    def __call__(self, notify):
        self.threads.append(threading.current_thread().name)
        if self.fail:
            raise OSError("no audio service")
        self.notify = notify

        def unsubscribe():
            self.threads.append(threading.current_thread().name)
            self.unsubscribed += 1

        return unsubscribe


def started(subscription):
    watch = DeviceWatch(subscribe=subscription)
    watch.start()
    deadline = time.monotonic() + 2
    while not watch.active and watch._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    return watch


def rebuilds():
    calls = []

    def rebuild():
        calls.append(1)
        return ["Microphone (Usb Audio Device)"]

    return calls, rebuild


def test_the_list_is_owed_from_the_start_and_built_once():
    watch = started(Subscription())
    calls, rebuild = rebuilds()
    assert watch.refresh(False, rebuild) == ["Microphone (Usb Audio Device)"]
    assert watch.refresh(False, rebuild) is None
    assert watch.refresh(True, rebuild) is None       # the timer no longer rebuilds anything
    assert calls == [1]
    watch.stop()


def test_a_notification_makes_the_list_due_again():
    subscription = Subscription()
    watch = started(subscription)
    calls, rebuild = rebuilds()
    watch.refresh(False, rebuild)
    subscription.notify()
    subscription.notify()                              # a plug-in comes as a burst of events
    assert watch.pending()
    assert watch.refresh(False, rebuild) is not None
    assert watch.refresh(False, rebuild) is None
    assert calls == [1, 1]
    watch.stop()


def test_a_list_that_could_not_be_rebuilt_stays_owed():
    watch = started(Subscription())
    assert watch.refresh(False, lambda: None) is None   # a recording is running
    assert watch.pending()
    calls, rebuild = rebuilds()
    assert watch.refresh(False, rebuild) is not None
    assert calls == [1]
    watch.stop()


def test_a_change_reported_during_the_rebuild_is_not_lost():
    subscription = Subscription()
    watch = started(subscription)

    def rebuild():
        subscription.notify()                          # something else changed meanwhile
        return ["one"]

    watch.refresh(False, rebuild)
    assert watch.pending()
    watch.stop()


def test_without_notifications_the_list_is_rebuilt_on_the_timer(caplog):
    with caplog.at_level("ERROR", logger="tolmach"):
        watch = started(Subscription(fail=True))
        watch._thread.join(2.0)
    assert watch.active is False
    assert "polled" in caplog.text
    calls, rebuild = rebuilds()
    assert watch.refresh(False, rebuild) is None
    assert watch.refresh(True, rebuild) is not None
    assert watch.refresh(True, rebuild) is not None
    assert calls == [1, 1]
    watch.stop()


def test_before_the_subscription_is_up_the_timer_still_builds_the_list():
    watch = DeviceWatch(subscribe=Subscription())      # never started
    calls, rebuild = rebuilds()
    assert watch.refresh(True, rebuild) is not None
    assert calls == [1]


def test_stop_unsubscribes_on_the_thread_that_subscribed():
    subscription = Subscription()
    watch = started(subscription)
    watch.stop()
    assert subscription.unsubscribed == 1
    assert subscription.threads == ["devices", "devices"]
    assert watch.active is False
    assert not watch._thread.is_alive()


def test_stop_without_start_is_harmless():
    DeviceWatch(subscribe=Subscription()).stop()


def test_a_failing_unsubscribe_is_logged_not_raised(caplog):
    subscription = Subscription()

    def subscribe(notify):
        subscription(notify)

        def unsubscribe():
            raise OSError("already gone")

        return unsubscribe

    watch = started(subscribe)
    with caplog.at_level("ERROR", logger="tolmach"):
        watch.stop()
    assert not watch._thread.is_alive()
    assert any(record.exc_info for record in caplog.records)
