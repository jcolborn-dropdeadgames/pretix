"""
This code has been taken from
https://blog.hypertrack.io/2016/10/08/dealing-with-database-transactions-in-django-celery/

Usage:
    from pretix.base.services.async import TransactionAwareTask
    @task(base=TransactionAwareTask)
    def task_…():
"""
import cProfile
import os
import random
import time

from django.conf import settings
from django.db import transaction

from pretix.base.metrics import celery_task_runs, celery_task_times
from pretix.celery_app import app


class ProfiledTask(app.Task):
    def __call__(self, *args, **kwargs):

        if settings.PROFILING_RATE > 0 and random.random() < settings.PROFILING_RATE / 100:
            profiler = cProfile.Profile()
            profiler.enable()
            t0 = time.perf_counter()
            ret = super().__call__(*args, **kwargs)
            tottime = time.perf_counter() - t0
            profiler.disable()
            profiler.dump_stats(os.path.join(settings.PROFILE_DIR, '{time:.0f}_{tottime:.3f}_celery_{t}.pstat'.format(
                t=self.name, tottime=tottime, time=time.time()
            )))
        else:
            t0 = time.perf_counter()
            ret = super().__call__(*args, **kwargs)
            tottime = time.perf_counter() - t0

        if settings.METRICS_ENABLED:
            celery_task_times.observe(tottime, task_name=self.name)
        return ret

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        if settings.METRICS_ENABLED:
            expected = False
            for t in self.throws:
                if isinstance(exc, t):
                    expected = True
                    break
            celery_task_runs.inc(1, task_name=self.name, status="expected-error" if expected else "error")

        return super().on_failure(exc, task_id, args, kwargs, einfo)

    def on_success(self, retval, task_id, args, kwargs):
        if settings.METRICS_ENABLED:
            celery_task_runs.inc(1, task_name=self.name, status="success")

        return super().on_success(retval, task_id, args, kwargs)


class TransactionAwareTask(ProfiledTask):
    """
    Task class which is aware of django db transactions and only executes tasks
    after transaction has been committed
    """

    def apply_async(self, *args, **kwargs):
        """
        Unlike the default task in celery, this task does not return an async
        result
        """
        transaction.on_commit(
            lambda: super(TransactionAwareTask, self).apply_async(*args, **kwargs)
        )
