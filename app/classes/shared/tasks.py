import asyncio
import datetime
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, List, Optional, TypedDict, cast
from zoneinfo import ZoneInfoNotFoundError

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.job import Job
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from peewee import DoesNotExist
from requests.exceptions import JSONDecodeError
from tzlocal import get_localzone

from app.classes.controllers.users_controller import UsersController
from app.classes.helpers.file_helpers import FileHelpers
from app.classes.helpers.helpers import Helpers
from app.classes.models.management import HelpersManagement, Schedules
from app.classes.models.users import HelperUsers
from app.classes.shared.console import Console
from app.classes.shared.main_controller import Controller
from app.classes.shared.websocket_manager import WebSocketManager
from app.classes.web.tornado_handler import Webserver

logger = logging.getLogger("apscheduler")
command_log = logging.getLogger("cmd_queue")
scheduler_intervals = {
    "seconds",
    "minutes",
    "hours",
    "days",
    "weeks",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
}

FAILED_DB_IMPORT_MESSAGE = "Removing failed task from DB."
SCHEDULE_DATE_STRING_FORMAT = "%m/%d/%Y, %H:%M:%S"


class ScheduleJobData(TypedDict):
    server_id: str
    action: str
    interval: int
    interval_type: str
    start_time: str
    command: str | None
    name: str
    enabled: bool
    one_time: bool
    cron_string: str
    parent: int | None
    delay: int
    action_id: Optional[str | None]


class QueuedCommandData(TypedDict):
    server_id: str
    user_id: int
    command: str | None
    action_id: str | None


class TasksManager:
    controller: Controller

    def __init__(self, helper, controller, file_helper):
        self.helper: Helpers = helper
        self.controller: Controller = controller
        self.tornado: Webserver = Webserver(helper, controller, self, file_helper)
        try:
            self.tz = get_localzone()
        except ZoneInfoNotFoundError as e:
            logger.exception(
                "Could not capture time zone from system. Falling back to Europe/London"
                f" error: {e}"
            )
            self.tz = "Europe/London"
        self.scheduler = BackgroundScheduler(timezone=str(self.tz))

        self.users_controller: UsersController = self.controller.users

        self.webserver_thread = threading.Thread(
            target=self.tornado.run_tornado, daemon=True, name="tornado_thread"
        )

        self.main_thread_exiting = False

        self.schedule_thread = threading.Thread(
            target=self.scheduler_thread, daemon=True, name="scheduler"
        )

        self.log_watcher_thread = threading.Thread(
            target=self.log_watcher, daemon=True, name="log_watcher"
        )

        self.command_thread = threading.Thread(
            target=self.command_watcher, daemon=True, name="command_watcher"
        )

        self.realtime_thread = threading.Thread(
            target=self.realtime, daemon=True, name="realtime"
        )

        self.reload_schedule_from_db()

    def get_main_thread_run_status(self):
        return self.main_thread_exiting

    @staticmethod
    def reload_schedule_from_db() -> None:
        """Reloads schedules using a helper and logs all enabled schedules"""
        jobs = HelpersManagement.get_schedules_enabled()
        logger.info("Reload from DB called. Current enabled schedules: ")
        for item in jobs:
            logger.info(f"JOB: {item}")

    def command_watcher(self) -> None:
        """Process queued server commands in the background

        Polls the management command queue and runs the tasks associated. Right now this
        is done in a while True loop. We may want to move this to a context aware cancel
        at some point rather than a simple while True.

        Commands are expected to provide server_id, user_id, and command keys. Backup
        commands also use action_id. If a queued command references a server that is no
        longer loaded, the command is logged and discarded.

        Returns:
            None
        """
        while True:
            # select any commands waiting to be processed
            command_log.debug(
                "Queue currently has "
                f"{self.controller.management.command_queue.qsize()} queued commands."
            )
            # Unwrap indentation block
            if self.controller.management.command_queue.empty():
                time.sleep(1)
                continue

            command_log.info(
                "Current queued commands: "
                f"{list(self.controller.management.command_queue.queue)}"
            )
            cmd = self.controller.management.command_queue.get()
            try:
                svr = self.controller.servers.get_server_instance_by_id(
                    cmd["server_id"]
                )
            # get_server_instance can raise ValueError when no loaded server matches
            # given server ID. Narrowed scope from just a bare except.
            except ValueError:
                logger.error(
                    f"Server value {cmd['server_id']} requested does not exist! "
                    "Purging item from waiting commands."
                )
                continue

            user_id = cmd["user_id"]
            command = cmd["command"]
            match command:
                case "start_server":
                    svr.run_threaded_server(user_id)
                case "stop_server":
                    svr.stop_threaded_server()
                case "restart_server":
                    svr.restart_threaded_server(user_id)
                case "kill_server":
                    try:
                        svr.kill()
                        time.sleep(5)
                        svr.cleanup_server_object()
                        svr.record_server_stats()
                    except Exception as e:
                        logger.exception(
                            f"Could not find PID for requested termsig. Full error: {e}"
                        )
                case "backup_server":
                    try:
                        svr.server_backup_threader(cmd["action_id"])
                    except (KeyError, DoesNotExist) as why:
                        logger.exception(
                            "Failed to run server backup on schedule with error %s",
                            why,
                        )
                case "update_executable":
                    svr.server_upgrade()
                case _:
                    try:
                        svr.send_command(command)
                    except AttributeError:
                        WebSocketManager().broadcast_to_server_users(
                            svr.server_id,
                            "send_error",
                            "Unable to send commands to a offline server",
                        )

            time.sleep(1)

    def _main_graceful_exit(self) -> None:
        """Shutdown all servers and remove all temporary/runtime files

        Removes the session lock file, sends the call to stop all servers, and delete
        all temporary files and directories. This function also sends the shutdown
        messages to the log and console. This function also sets main_thread_exiting to
        True.

        Returns:
            None
        """
        try:
            os.remove(self.helper.session_file)
        except OSError as why:
            logger.warning(
                f"Caught error deleting session file during shutdown: {why}",
                exc_info=True,
            )

        # Shutting down servers is capable of throwing many different errors. This may
        # need to be handled in subclasses rather than here. A quick review of sub-calls
        # shows ValueError, RuntimeError, BrokenPipeError, and OSError are all
        # possible.
        try:
            self.controller.servers.stop_all_servers()
        except:
            logger.info("Caught error during shutdown", exc_info=True)

        try:
            self.controller.network.shutdown()
        except Exception:
            logger.info("Caught error stopping network manager", exc_info=True)

        try:
            temp_dir = os.path.join(self.controller.project_root, "temp")
            FileHelpers.del_dirs(temp_dir)
        except OSError:
            logger.info(
                "Caught error during shutdown - "
                "unable to delete files from Crafty Temp Dir",
                exc_info=True,
            )

        logger.info("***** Crafty Shutting Down *****\n\n")
        Console.info("***** Crafty Shutting Down *****\n\n")
        self.main_thread_exiting = True

    def start_webserver(self):
        self.webserver_thread.start()

    def reload_webserver(self):
        self.tornado.stop_web_server()
        Console.info("Waiting 3 seconds")
        time.sleep(3)
        self.webserver_thread = threading.Thread(
            target=self.tornado.run_tornado, daemon=True, name="tornado_thread"
        )
        self.start_webserver()

    def stop_webserver(self):
        self.tornado.stop_web_server()

    def start_scheduler(self):
        logger.info("Launching Scheduler Thread...")
        Console.info("Launching Scheduler Thread...")
        self.schedule_thread.start()
        logger.info("Launching command thread...")
        Console.info("Launching command thread...")
        self.command_thread.start()
        logger.info("Launching log watcher...")
        Console.info("Launching log watcher...")
        self.log_watcher_thread.start()
        logger.info("Launching realtime thread...")
        Console.info("Launching realtime thread...")
        self.realtime_thread.start()

    def add_scheduler_jobs(self) -> None:
        """Helper function for scheduler_thread to add jobs to schedule

        Functionality used to be part of scheduler_thread. Pulled this out to simplify
        that function.
        """
        self.scheduler.add_job(
            self.crafty_maintenance,
            "interval",
            hours=12,
            id="update_watcher",
            start_date=datetime.datetime.now(),
        )
        self.scheduler.add_job(
            self.controller.write_auth_tracker,
            "interval",
            minutes=5,
            id="auth_tracker_write",
            start_date=datetime.datetime.now(),
        )
        self.scheduler.add_job(
            self.controller.totp.purge_pending,
            "interval",
            hours=24,
            id="mfa_purge",
            start_date=datetime.datetime.now(),
        )
        self.scheduler.add_job(
            self.controller.passkey.purge_expired_challenges,
            "interval",
            hours=1,
            id="passkey_challenge_purge",
            start_date=datetime.datetime.now(),
        )

    def _add_scheduler_command_job(
        self,
        sch_id: int,
        command_data: QueuedCommandData,
        interval: int | str,
        interval_type: str,
        start_time: str,
        cron_string: str,
    ) -> Job:
        """Add a queue_command job to APScheduler from normalized schedule values.

        The caller is responsible for deciding whether reaction or unsupported
        schedule types should be skipped, and for handling any exceptions raised
        while creating the scheduler job.
        """
        command_args = [command_data]
        if cron_string != "":
            return cast(
                Job,
                self.scheduler.add_job(
                    self.controller.management.queue_command,
                    CronTrigger.from_crontab(cron_string, timezone=str(self.tz)),
                    id=str(sch_id),
                    args=command_args,
                ),
            )

        trigger = "interval"
        trigger_kwargs: dict[str, Any] = {interval_type: int(interval)}

        if interval_type == "days":
            curr_time = start_time.split(":")
            trigger = "cron"
            trigger_kwargs = {
                "day": f"*/{interval}",
                "hour": curr_time[0],
                "minute": curr_time[1],
            }

        return cast(
            Job,
            self.scheduler.add_job(
                self.controller.management.queue_command,
                trigger,
                id=str(sch_id),
                args=command_args,
                **trigger_kwargs,
            ),
        )

    def _add_db_schedule(self, schedule: Schedules, system_user_id: int) -> Job | None:
        """Add a persisted schedule to APScheduler if its trigger is supported.

        Returns:
            Job added to scheduler if the job is not a reaction task
            None if the requested scheduled task is a reaction task
        """
        schedule_id = cast(int, schedule.schedule_id)
        interval_value = cast(int | str, schedule.interval)
        interval_type = cast(str, schedule.interval_type)
        cron_string = cast(str, schedule.cron_string)

        if interval_value == "reaction":
            return None

        command_data: QueuedCommandData = {
            "server_id": schedule.server_id.server_id,
            "user_id": system_user_id,
            "command": cast(str | None, schedule.command),
            "action_id": cast(str | None, schedule.action_id),
        }
        if cron_string != "":
            try:
                return self._add_scheduler_command_job(
                    schedule_id,
                    command_data,
                    interval_value,
                    interval_type,
                    cast(str, schedule.start_time),
                    cron_string,
                )
            except Exception as e:
                Console.error(f"Failed to schedule task with error: {e}.")
                Console.warning(FAILED_DB_IMPORT_MESSAGE)
                logger.exception(f"Failed to schedule task with error: {e}.")
                logger.warning(FAILED_DB_IMPORT_MESSAGE)
                # remove items from DB if task fails to add to apscheduler
                self.controller.management_helper.delete_scheduled_task(schedule_id)
                return None

        if interval_type not in {"hours", "minutes", "days"}:
            logger.warning(
                "Skipping schedule %s with unsupported interval_type %r",
                schedule_id,
                interval_type,
            )
            return None

        return self._add_scheduler_command_job(
            schedule_id,
            command_data,
            interval_value,
            interval_type,
            cast(str, schedule.start_time),
            cron_string,
        )

    def scheduler_thread(self) -> None:
        """Gets list of all scheduled tasks and adds them to the schedule.

        Parses the interval of each task and adds them to the scheduler.

        Returns:
            None
        """
        schedules = HelpersManagement.get_schedules_enabled()
        self.scheduler.add_listener(self.schedule_watcher, mask=EVENT_JOB_EXECUTED)
        self.scheduler.start()
        self.crafty_maintenance()
        self.add_scheduler_jobs()
        system_user_id = self.users_controller.get_id_by_name("system")

        # load schedules from DB
        for schedule in schedules:
            new_job = self._add_db_schedule(schedule, system_user_id)
            if new_job is None:
                continue

            task = self.controller.management.get_scheduled_task_model(int(new_job.id))
            self.controller.management.update_scheduled_task(
                task.schedule_id,
                {
                    "next_run": str(
                        new_job.next_run_time.strftime(SCHEDULE_DATE_STRING_FORMAT)
                    )
                },
            )
        jobs = self.scheduler.get_jobs()
        logger.info("Loaded schedules. Current enabled schedules: ")
        for item in jobs:
            logger.info(f"JOB: {item}")

    def schedule_job(self, job_data: ScheduleJobData) -> int | None:
        """Create a new persisted schedule and add it to schedule

        Unlike scheduler_thread, which reloads existing enabled schedules from the
        database during startup, this method handles a single new schedule payload. It
        creates the database row first, then immediately adds the schedule.

        Args:
            job_data: Validated schedule payload for a newly created task.

        Returns:
            The schedule ID when an enabled non-reaction task is successfully
            scheduled. Returns None for disabled tasks, reaction tasks, and
            schedule submissions that fail while being added to APScheduler.
        """
        sch_id = HelpersManagement.create_scheduled_task(
            job_data["server_id"],
            job_data["action"],
            job_data["interval"],
            job_data["interval_type"],
            job_data["start_time"],
            job_data["command"],
            job_data["name"],
            job_data["enabled"],
            job_data["one_time"],
            job_data["cron_string"],
            job_data["parent"],
            job_data["delay"],
            job_data.get("action_id", None),
        )

        # Checks to make sure some doofus didn't actually make the newly
        # created task a child of itself.
        if (
            str(job_data["parent"]) == str(sch_id)
            or job_data["interval_type"] != "reaction"
        ):
            HelpersManagement.update_scheduled_task(sch_id, {"parent": None})

        # Check to see if it's enabled and is not a chain reaction.
        if not job_data["enabled"] or job_data["interval_type"] == "reaction":
            return None

        # Let's make sure this can not be mistaken for a reaction
        job_data["parent"] = None
        system_user_id = self.users_controller.get_id_by_name("system")
        command_data: QueuedCommandData = {
            "server_id": job_data["server_id"],
            "user_id": system_user_id,
            "command": job_data["command"],
            "action_id": job_data.get("action_id", None),
        }
        new_job = "error"
        if job_data["cron_string"] != "":
            try:
                new_job = self._add_scheduler_command_job(
                    sch_id,
                    command_data,
                    job_data["interval"],
                    job_data["interval_type"],
                    job_data["start_time"],
                    job_data["cron_string"],
                )
            except Exception as e:
                new_job = "error"
                Console.error(f"Failed to schedule task with error: {e}.")
                Console.warning(FAILED_DB_IMPORT_MESSAGE)
                logger.exception(f"Failed to schedule task with error: {e}.")
                logger.warning(FAILED_DB_IMPORT_MESSAGE)
                # remove items from DB if task fails to add to apscheduler
                self.controller.management_helper.delete_scheduled_task(sch_id)
        else:
            if job_data["interval_type"] in {"hours", "minutes", "days"}:
                new_job = self._add_scheduler_command_job(
                    sch_id,
                    command_data,
                    job_data["interval"],
                    job_data["interval_type"],
                    job_data["start_time"],
                    job_data["cron_string"],
                )
        logger.info("Added job. Current enabled schedules: ")
        jobs = self.scheduler.get_jobs()
        if new_job == "error":
            for item in jobs:
                logger.info(f"JOB: {item}")
            return None

        self.controller.management.update_scheduled_task(
            sch_id,
            {"next_run": new_job.next_run_time.strftime(SCHEDULE_DATE_STRING_FORMAT)},
        )
        for item in jobs:
            logger.info(f"JOB: {item}")
        return sch_id

    def remove_all_server_tasks(self, server_id):
        schedules = HelpersManagement.get_schedules_by_server(server_id)
        for schedule in schedules:
            if schedule.interval != "reaction":
                self.remove_job(schedule.schedule_id)

    def remove_job(self, sch_id):
        job = HelpersManagement.get_scheduled_task_model(sch_id)
        for schedule in HelpersManagement.get_child_schedules(sch_id):
            self.controller.management_helper.update_scheduled_task(
                schedule.schedule_id, {"parent": None}
            )
        self.controller.management_helper.delete_scheduled_task(sch_id)
        if job.enabled and job.interval_type != "reaction":
            self.scheduler.remove_job(str(sch_id))
            logger.info(f"Job with ID {sch_id} was deleted.")
        else:
            logger.info(
                f"Job with ID {sch_id} was deleted from DB, but was not enabled."
                f"Not going to try removing something "
                f"that doesn't exist from active schedules."
            )

    def _remove_scheduler_job_if_present(
        self, sch_id: int, missing_log_message: str | None = None
    ) -> bool:
        """Remove a scheduler job if it exists.

        Returns:
            True when the job was removed.
            False when scheduler had no job for the provided ID.
        """
        try:
            self.scheduler.remove_job(str(sch_id))
        except JobLookupError:
            if missing_log_message is not None:
                logger.info(missing_log_message)
            return False
        return True

    def _normalize_update_job_data(
        self, sch_id: int, job_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extracted confirming of keys in dict from update_job

        Returns:
            None if dict is not sufficient for update_job
            Dict data if all checks pass for update_job
        """
        required_keys = {"interval", "enabled", "cron_string", "interval_type"}
        if required_keys.issubset(job_data):
            return job_data

        if "enabled" not in job_data:
            return None

        if job_data["enabled"] is True:
            full_job_data = HelpersManagement.get_scheduled_task(sch_id)
            full_job_data["server_id"] = full_job_data["server_id"]["server_id"]
            return full_job_data

        full_job_data = HelpersManagement.get_scheduled_task(sch_id)
        if full_job_data["interval_type"] != "reaction":
            self._remove_scheduler_job_if_present(sch_id)
        return None

    def run_task_now(self, schedule_id, user_id, server_id, cascade=False):
        """Immediately queue a scheduled task's action for execution.

        Looks up the task, confirms it belongs to the given server, and places
        its command on the management command queue so it runs through the same
        path as a normally scheduled job.

        Args:
            schedule_id: ID of the scheduled task to run.
            user_id: ID of the user requesting the run (recorded with the command).
            server_id: ID of the server the task is expected to belong to.
            cascade: When True, also trigger any enabled reaction tasks chained
                to this task, after their configured delays. Defaults to False so
                a manual run executes only the selected task.

        Raises:
            ValueError: If the task does not belong to the given server.
        """
        task = self.controller.management.get_scheduled_task(schedule_id)
        task_server_id = task["server_id"]
        if isinstance(task_server_id, dict):
            task_server_id = task_server_id["server_id"]
        if str(task_server_id) != str(server_id):
            raise ValueError(
                f"Task {schedule_id} does not belong to server {server_id}"
            )
        self.controller.management.queue_command(
            {
                "server_id": task_server_id,
                "user_id": user_id,
                "command": task["command"],
                "action_id": task.get("action_id"),
            }
        )
        if cascade:
            self._trigger_child_schedules(schedule_id, task_server_id)

    def update_job(self, sch_id, job_data):
        # Checks to make sure some doofus didn't actually make the newly
        # created task a child of itself.
        interval_type = job_data.get("interval_type")
        if str(job_data.get("parent")) == str(sch_id) or interval_type != "reaction":
            job_data["parent"] = None
        HelpersManagement.update_scheduled_task(sch_id, job_data)

        job_data = self._normalize_update_job_data(sch_id, job_data)
        if job_data is None:
            return

        if job_data["interval_type"] != "reaction":
            self._remove_scheduler_job_if_present(
                sch_id,
                "No job found in update job. "
                "Assuming it was previously disabled. Starting new job.",
            )
        if job_data["enabled"] and (job_data["interval_type"] != "reaction"):
            command_data: QueuedCommandData = {
                "server_id": job_data["server_id"],
                "user_id": self.users_controller.get_id_by_name("system"),
                "command": job_data["command"],
                "action_id": job_data.get("action_id", None),
            }
            try:
                new_job = self._add_scheduler_command_job(
                    sch_id,
                    command_data,
                    job_data["interval"],
                    job_data["interval_type"],
                    job_data["start_time"],
                    job_data["cron_string"],
                )
            except Exception as e:
                new_job = "error"
                Console.error(f"Failed to schedule task with error: {e}.")
                Console.info(FAILED_DB_IMPORT_MESSAGE)
                self.controller.management_helper.delete_scheduled_task(sch_id)
            if new_job != "error":
                task = self.controller.management.get_scheduled_task_model(
                    int(new_job.id)
                )
                self.controller.management.update_scheduled_task(
                    task.schedule_id,
                    {
                        "next_run": new_job.next_run_time.strftime(
                            SCHEDULE_DATE_STRING_FORMAT
                        )
                    },
                )
        else:
            self._remove_scheduler_job_if_present(
                sch_id,
                f"APScheduler found no scheduled job on schedule update for "
                f"schedule with id: {sch_id} Assuming it was already disabled.",
            )

    def schedule_watcher(self, event):
        if event.exception:
            logger.error(f"Task failed with error: {event.exception}")
            return

        if not str(event.job_id).isnumeric():
            logger.info(
                "Event job ID is not numerical. Assuming it's stats "
                "- not stored in DB. Moving on."
            )
            return

        task = self.controller.management.get_scheduled_task_model(int(event.job_id))
        self.controller.management.add_to_audit_log_raw(
            "system",
            HelperUsers.get_user_id_by_name("system"),
            task.server_id,
            f"Task with id {task.schedule_id} completed successfully",
            "127.0.0.1",
        )
        # check if the task is a single run.
        if task.one_time:
            self.remove_job(task.schedule_id)
            logger.info("one time task detected. Deleting...")
        elif task.interval_type != "reaction":
            self.controller.management.update_scheduled_task(
                task.schedule_id,
                {
                    "next_run": self.scheduler.get_job(
                        event.job_id
                    ).next_run_time.strftime(SCHEDULE_DATE_STRING_FORMAT)
                },
            )
        # check for any child tasks for this. It's kind of backward,
        # but this makes DB management a lot easier. One to one
        # instead of one to many.
        self._trigger_child_schedules(task.schedule_id, task.server_id)

    def _trigger_child_schedules(self, parent_id, server_id):
        """Queue any enabled reaction tasks chained to a parent task.

        Reaction (child) tasks have no schedule of their own; they run only as
        a side effect of their parent task firing. Each matching child is queued
        after its configured delay, mirroring how a normally scheduled parent
        triggers its children.

        Args:
            parent_id: Schedule ID of the parent task that just ran.
            server_id: ID of the server the tasks belong to.
        """
        for schedule in HelpersManagement.get_child_schedules_by_server(
            parent_id, server_id
        ):
            # parent IDs are stored as strings, so compare as strings.
            if (
                str(schedule.parent) == str(parent_id)
                and schedule.interval_type == "reaction"
                and schedule.enabled
            ):
                delay_time = datetime.datetime.now() + datetime.timedelta(
                    seconds=schedule.delay
                )
                self.scheduler.add_job(
                    self.controller.management.queue_command,
                    "date",
                    run_date=delay_time,
                    id=str(schedule.schedule_id),
                    args=[
                        {
                            "server_id": schedule.server_id.server_id,
                            "user_id": self.users_controller.get_id_by_name("system"),
                            "command": schedule.command,
                            "action_id": schedule.action_id,
                        }
                    ],
                )

    def start_stats_recording(self):
        stats_update_frequency = self.helper.get_setting(
            "stats_update_frequency_seconds"
        )
        logger.info(
            f"Stats collection frequency set to {stats_update_frequency} seconds"
        )
        Console.info(
            f"Stats collection frequency set to {stats_update_frequency} seconds"
        )

        # one for now,
        self.controller.servers.stats.record_stats()
        # one for later
        self.scheduler.add_job(
            self.controller.servers.stats.record_stats,
            "interval",
            seconds=stats_update_frequency,
            id="stats",
        )

    def big_bucket_cache_refresher(self):
        logger.info("Refreshing big bucket cache on start")
        self.controller.big_bucket.refresh_cache()

        logger.info("Scheduling big bucket cache refresh service every 12 hours")
        self.scheduler.add_job(
            self.controller.big_bucket.refresh_cache,
            "interval",
            hours=12,
            id="big_bucket",
        )

    def realtime(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        host_stats = HelpersManagement.get_latest_hosts_stats()

        while True:
            if host_stats.get(
                "cpu_usage"
            ) != HelpersManagement.get_latest_hosts_stats().get(
                "cpu_usage"
            ) or host_stats.get(
                "mem_percent"
            ) != HelpersManagement.get_latest_hosts_stats().get(
                "mem_percent"
            ):
                # Stats are different

                host_stats = HelpersManagement.get_latest_hosts_stats()

                self.controller.management.cpu_usage.set(host_stats.get("cpu_usage"))
                self.controller.management.mem_usage_percent.set(
                    host_stats.get("mem_percent")
                )

                if len(WebSocketManager().clients) > 0:
                    # There are clients
                    try:
                        WebSocketManager().broadcast_page(
                            "/panel/dashboard",
                            "update_host_stats",
                            {
                                "cpu_usage": host_stats.get("cpu_usage"),
                                "cpu_cores": host_stats.get("cpu_cores"),
                                "cpu_cur_freq": host_stats.get("cpu_cur_freq"),
                                "cpu_max_freq": host_stats.get("cpu_max_freq"),
                                "mem_percent": host_stats.get("mem_percent"),
                                "mem_usage": host_stats.get("mem_usage"),
                                "mem_total": host_stats.get("mem_total"),
                                "disk_usage": json.loads(
                                    host_stats.get("disk_json").replace("'", '"')
                                ),
                                "mounts": self.helper.get_setting("monitored_mounts"),
                            },
                        )
                    # A quick review of the calls to the disk and mounts is that
                    # upstream catches any OS probe errors. We should only need to catch
                    # malformed JSON or missing keys that we are selecting for here.
                    except (JSONDecodeError, AttributeError, TypeError):
                        WebSocketManager().broadcast_page(
                            "/panel/dashboard",
                            "update_host_stats",
                            {
                                "cpu_usage": host_stats.get("cpu_usage"),
                                "cpu_cores": host_stats.get("cpu_cores"),
                                "cpu_cur_freq": host_stats.get("cpu_cur_freq"),
                                "cpu_max_freq": host_stats.get("cpu_max_freq"),
                                "mem_percent": host_stats.get("mem_percent"),
                                "mem_usage": host_stats.get("mem_usage"),
                                "mem_total": host_stats.get("mem_total"),
                                "disk_usage": {},
                            },
                        )
            time.sleep(1)

    def crafty_maintenance(self) -> None:
        """Maintenance tasks for Crafty, runs every 12 hours and on startup

        Runs: Update check, Gravitar PFP update, and clearing of import temp dir.

        Returns:
            None
        """
        self.check_for_updates()
        self.refresh_gravatar()
        self.clean_import_directory()

    def refresh_gravatar(self) -> None:
        """Updates user Gravatar PFPs"""
        logger.info("Refreshing Gravatar PFPs...")
        for user in HelperUsers.get_all_users():
            if user.email:
                HelperUsers.update_user(
                    user.id, {"pfp": self.helper.get_gravatar_image(user.email)}
                )

    def clean_import_directory(self) -> None:
        """Removes all temporary files in Crafty

        Will check for OSError on removal but will not raise errors, only log them.

        Returns:
            None
        """
        # Search for old files in imports
        self.helper.ensure_dir_exists(
            os.path.join(self.controller.project_root, "import", "upload")
        )
        self.helper.ensure_dir_exists(
            os.path.join(self.controller.project_root, "temp")
        )

        # Setup pathlib objects to help iterate over files
        removals: List[Path] = []
        temp_path = Path(self.controller.project_root, "temp")
        import_path = Path(self.controller.project_root, "import", "upload")

        # Check temp path for files
        for path in temp_path.iterdir():
            if self.helper.is_file_older_than_x_days(path):
                removals.append(path)

        # Check import path for files
        for path in import_path.iterdir():
            try:
                path = Path(self.helper.validate_traversal(import_path, path))
            except ValueError:
                logger.error("Traversal detected while deleting import file %s", path)
                continue

            if self.helper.is_file_older_than_x_days(path):
                removals.append(path)

        # Remove everything found.
        for path_to_remove in removals:
            try:
                if path_to_remove.is_dir():
                    FileHelpers.del_dirs(path_to_remove)
                else:
                    path_to_remove.unlink()
            except OSError as why:
                logger.exception(f"Error removing file {path_to_remove}: {why}.")

    def check_for_updates(self) -> None:
        """Checks for updates to crafty

        Returns:
            None
        """
        logger.info("Checking for Crafty updates...")
        self.helper.update_available = self.helper.check_remote_version()
        remote = self.helper.update_available
        if self.helper.update_available:
            logger.info(f"Found new version {self.helper.update_available}")
        else:
            logger.info(
                "No updates found! You are on the most up to date Crafty version."
            )
        if self.helper.update_available:
            self.helper.update_available = {
                "id": str(remote),
                "title": f"{remote} Update Available",
                "date": "",
                "desc": "Release notes are available by clicking this notification.",
                "link": "https://gitlab.com/crafty-controller/crafty-4/-/releases",
            }

    def log_watcher(self):
        self.check_for_old_logs()
        self.scheduler.add_job(
            self.check_for_old_logs,
            "interval",
            hours=6,
            id="log-mgmt",
        )

    def check_for_old_logs(self):
        # check for server logs first
        self.controller.servers.check_for_old_logs()
        try:
            # check for crafty logs now
            logs_path = os.path.join(self.controller.project_root, "logs")
            logs_delete_after = int(
                self.helper.get_setting("crafty_logs_delete_after_days")
            )
            latest_log_files = [
                "session.log",
                "schedule.log",
                "tornado-access.log",
                "session.log",
                "commander.log",
            ]
            # we won't delete if delete logs after is set to 0
            if logs_delete_after != 0:
                log_files = list(
                    filter(
                        lambda val: val not in latest_log_files,
                        os.listdir(logs_path),
                    )
                )
                for log_file in log_files:
                    log_file_path = os.path.join(logs_path, log_file)
                    if Helpers.check_file_exists(
                        log_file_path
                    ) and Helpers.is_file_older_than_x_days(
                        log_file_path, logs_delete_after
                    ):
                        os.remove(log_file_path)
        # A quick look at the sub-calls looks like there are a lot of options for
        # possible exceptions here. I see TypeError, AttributeError, FileNotFoundError,
        # NotADirectoryError, etc. For the current state in Crafty we'll need to leave
        # this as a bare except. We'll need to implement better error handling in lower
        # level call areas of Crafty to reasonably narrow this scope.
        except:
            logger.debug(
                "Unable to find project root."
                " If this issue persists please contact support."
            )
