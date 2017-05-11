from typing import *
from gi.repository import GLib, Gtk
import logging
from apartcore import ApartCore, MessageListener
import historic_job
from historic_job import FinishedJob
import running_job
from running_job import RunningJob
import settings

log = logging.getLogger('ProgressAndHistoryView')


class ProgressAndHistoryView(Gtk.Stack):
    def __init__(self, core: ApartCore):
        Gtk.Stack.__init__(self)
        self.core = core
        self.get_style_context().add_class('progress-view')

        self.nothing_label = Gtk.Label('Select a partition to clone', xalign=0.5, vexpand=True)
        self.nothing_label.get_style_context().add_class('dim-label')
        self.add(self.nothing_label)

        self.content = Gtk.VBox(valign=Gtk.Align.START)
        self.add(self.content)

        self.running_jobs_label = Gtk.Label('Running', halign=Gtk.Align.START)
        self.running_jobs_label.get_style_context().add_class('section-title')
        self.content.add(self.running_jobs_label)

        # self.running_jobs: Dict[str, RunningJob] = {} <- not compatiable with 3.5
        self.running_jobs = {}
        self.running_jobs_grid = Gtk.Grid(orientation=Gtk.Orientation.VERTICAL,
                                          column_spacing=6,
                                          row_spacing=6)

        self.running_jobs_grid.get_style_context().add_class('jobs')
        self.content.add(self.running_jobs_grid)

        # self.finished_jobs: Dict[str, FinishedJob] = {} <- not compatiable with 3.5
        self.finished_jobs = {}
        self.finished_jobs_label = Gtk.Label('History', halign=Gtk.Align.START)
        self.finished_jobs_label.get_style_context().add_class('section-title')
        self.content.add(self.finished_jobs_label)

        self.finished_jobs_grid = Gtk.Grid(orientation=Gtk.Orientation.VERTICAL,
                                           column_spacing=6)
        self.finished_jobs_grid.get_style_context().add_class('finished-jobs')
        self.content.add(self.finished_jobs_grid)

        self.show_all()

        self.listener = MessageListener(message_predicate=lambda m: m['type'] in ['clone',
                                                                                  'restore',
                                                                                  'clone-failed',
                                                                                  'restore-failed'],
                                        on_message=lambda m: GLib.idle_add(self.on_job_message, m),
                                        listen_to=core)

        GLib.timeout_add(interval=1000, function=self.update_jobs)
        self.connect('destroy', self.save_history)
        GLib.idle_add(self.read_history)

    def read_history(self):
        for historic_job_msg in settings.read_history():
            try:
                self.finished_jobs[historic_job_msg['id']] = historic_job.create(historic_job_msg,
                                                                                 progress_view=self,
                                                                                 core=self.core)
            except KeyError as e:
                log.warning('Error constructing FinishedJob from historic data ' + str(e))

        for job in sorted(self.finished_jobs.values(),
                          key=lambda j: j.finish,
                          reverse=True):
            job.add_to_grid(self.finished_jobs_grid)
        self.update_view()

    def new_running_job(self, msg: Dict) -> RunningJob:
        job = running_job.create(msg, self.core, on_finish=self.on_job_finish)
        self.running_jobs[msg['id']] = job
        job.add_to_grid(self.running_jobs_grid)
        return job

    def on_job_message(self, msg: Dict):
        job = self.running_jobs.get(msg['id']) or self.new_running_job(msg)
        job.handle_message(msg)
        self.update_view()

    def update_view(self):
        if self.running_jobs or self.finished_jobs:
            self.set_visible_child(self.content)
        else:
            self.set_visible_child(self.nothing_label)
        self.finished_jobs_label.set_visible(not not self.finished_jobs)
        self.finished_jobs_grid.set_visible(not not self.finished_jobs)
        self.running_jobs_label.set_visible(not not self.running_jobs)
        self.running_jobs_grid.set_visible(not not self.running_jobs)

    def update_jobs(self) -> bool:
        for job in self.running_jobs.values():
            job.update()
        for job in self.finished_jobs.values():
            job.update()
        return True

    def on_job_finish(self, final_msg: Dict):
        job_id = final_msg['id']
        job = self.running_jobs[job_id]
        job.remove_from_grid()
        del self.running_jobs[job_id]
        job = historic_job.create(final_msg, progress_view=self, core=self.core)
        job.reveal_extra()  # show extra details of newest finished job

        # remove all and re-add for ordering
        to_remove = []
        for id, existing_job in self.finished_jobs.items():
            existing_job.remove_from_grid()
            if existing_job.similar_to(job):
                to_remove.append(id)

        for id in to_remove:
            del self.finished_jobs[id]

        self.finished_jobs[job_id] = job
        for finished_job in sorted(self.finished_jobs.values(),
                                   key=lambda x: x.finish,
                                   reverse=True):
            finished_job.add_to_grid(self.finished_jobs_grid)
            if finished_job != job:
                finished_job.default_extra_reveal()

        self.update_view()

    def forget(self, job: FinishedJob):
        del self.finished_jobs[job.msg['id']]
        job.remove_from_grid()
        self.update_view()

    def save_history(self, arg=None):
        history = list(map(lambda j: j.msg, self.finished_jobs.values()))
        settings.write_history(history)
