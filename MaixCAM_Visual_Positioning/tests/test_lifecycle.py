"""Resource owners must exit before capture/menu ownership changes."""
import importlib.util
import io
import signal
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

spec=importlib.util.spec_from_file_location('vision_agent',Path(__file__).resolve().parents[1]/'device'/'vision_agent.py')
agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)


class LifecycleTests(unittest.TestCase):
    @patch.object(agent.signal,'SIGKILL',9,create=True)
    def test_transient_driver_wait_is_allowed_to_exit_normally(self):
        with patch.object(agent,'process_state',side_effect=['D','S',None]),patch.object(agent.os,'kill') as kill,patch.object(agent.time,'sleep'):
            agent.stop_processes([123])
        kill.assert_called_once_with(123,signal.SIGTERM)

    @patch.object(agent.signal,'SIGKILL',9,create=True)
    def test_persistently_nonexiting_owner_times_out_without_starting_capture(self):
        ticks=iter([0,5,5,8])
        with patch.object(agent,'process_state',return_value='D'),patch.object(agent.os,'kill') as kill,patch.object(agent.time,'monotonic',side_effect=lambda:next(ticks)):
            with self.assertRaisesRegex(RuntimeError,'尚未退出'):agent.stop_processes([123])
        self.assertEqual([c.args for c in kill.call_args_list],[(123,signal.SIGTERM),(123,9)])

    @patch.object(agent.signal,'SIGKILL',9,create=True)
    def test_graceful_stop_waits_for_process_exit(self):
        with patch.object(agent,'process_state',side_effect=['S',None]),patch.object(agent.os,'kill') as kill,patch.object(agent.time,'sleep') as sleep:
            agent.stop_processes([123])
        kill.assert_called_once_with(123,signal.SIGTERM);sleep.assert_called_once()

    def test_takeover_stops_supervisor_then_agent_then_startup_app(self):
        folder='/root/aic_vision';events=[]
        def stop(pids):events.append(list(pids))
        cmd=b'python3\0'+agent.os.path.join(folder,'vision_agent.py').encode()+b'\0'
        with patch.object(agent,'stock_processes',side_effect=[[100],[101],[102]]),patch.object(agent,'stop_processes',side_effect=stop),patch.object(agent,'process_state',return_value='S'),patch('builtins.open',side_effect=[io.StringIO('123'),io.BytesIO(cmd)]):
            agent.prepare_device(folder)
        self.assertEqual(events,[[100],[123],[101],[102]])

    def test_menu_restore_waits_for_native_process_exit(self):
        with patch.object(agent,'process_state',side_effect=['S',None]),patch.object(agent.time,'sleep') as sleep,patch.object(agent,'stock_processes',return_value=[]),patch.object(agent.subprocess,'Popen') as start:
            agent.restore_after_exit(123)
        sleep.assert_called_once();self.assertEqual(start.call_args.args[0],[agent.LAUNCHER])

    def test_menu_restore_does_not_duplicate_existing_supervisor(self):
        with patch.object(agent,'process_state',return_value=None),patch.object(agent,'stock_processes',return_value=[100]),patch.object(agent.subprocess,'Popen') as start:
            agent.restore_after_exit(123)
        start.assert_not_called()

    def test_takeover_signal_suppresses_menu_restart(self):
        try:
            agent.restore_menu=True;agent.stop.clear();agent.handle_signal()
            self.assertTrue(agent.stop.is_set());self.assertFalse(agent.restore_menu)
        finally:agent.restore_menu=True;agent.stop.clear()


if __name__=='__main__':unittest.main()
