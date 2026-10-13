"""Caller tasks for MediaJobs. No queue or worker authority lives here."""
import asyncio
import logging

from motion_jobs import Cancelled, validate_result

LOG = logging.getLogger(__name__)


def submit_owned(gate, job, operation, check_project, *, publish_result=None):
    """Submit/create/bind atomically; operation owns all stage teardown."""
    ticket = gate.submit(job)
    task = asyncio.create_task(_run(gate, ticket, operation, check_project, publish_result))
    try:
        gate.bind(ticket, task)
    except BaseException:
        task.cancel()
        gate.cancel(*ticket.key[:2], project=ticket.key[2], actor=ticket.key[3])
        raise
    return ticket


async def _run(gate, ticket, operation, check_project, publish_result):
    lease, result, error, phase = None, None, None, 'cancelled'

    def check():
        ticket.check_cancel()
        check_project()

    try:
        lease = await gate.wait(ticket, check)
        check()
        result = validate_result(await operation(ticket, check))
        check()
        if publish_result is not None:
            ticket.job['phase'] = 'publishing'
            await publish_result(result)
            check()
        phase = 'done'
    except Cancelled:
        pass
    except asyncio.CancelledError:
        ticket.stop.set()
        ticket.job['cancel'] = True
        raise
    except Exception as exc:
        phase, error = 'failed', str(exc) or type(exc).__name__
        LOG.warning('Media %s job %s failed', ticket.job['kind'], ticket.key[0], exc_info=True)
    finally:
        # The operation's finally has already joined its own workers and writes.
        # wait() handles cancellation before admission, without owned resources.
        if lease is not None:
            gate.finish(lease, phase=phase, result=result if phase == 'done' else None, error=error)
