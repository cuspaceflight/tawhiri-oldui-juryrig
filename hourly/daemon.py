#!/usr/bin/env python

import logging
import logging.handlers
import os
import os.path
import time
import datetime
import uuid
import subprocess
import json
import calendar
import shutil
import traceback
import pyinotify

def last_line_of(filename):
    file = open(filename, 'r')
    last_line = ''
    for line in file:
        last_line = line
    return last_line.strip()

class DatasetRaceError(Exception):
    pass

def run_prediction(prediction_time, predictions_dir, scenario_template,
                   dataset_filename, dataset_time, root):
    pred_binary = os.path.join(root, "pred_src", "pred")

    # Create a UUID for the prediction
    pred_uuid = uuid.uuid4()

    # Create a directory for the predicition result.
    pred_root = os.path.join(predictions_dir, str(pred_uuid))
    os.mkdir(pred_root)

    scenario = {
        "launch-site": scenario_template["launch-site"],
        "altitude-model": scenario_template["altitude-model"],
        "launch-time": {
            'year': prediction_time.year,
            'month': prediction_time.month,
            'day': prediction_time.day,
            'hour': prediction_time.hour,
            'minute': prediction_time.minute,
            'second': prediction_time.second,
        }
    }

    logging.debug('Using scenario:')
    logging.debug(scenario)

    # Convert the scenario to an INI
    scenarioINI = []
    for cattitle, catcontents in scenario.iteritems():
        scenarioINI.append('[%s]' % cattitle)
        for key, value in catcontents.iteritems():
            scenarioINI.append('%s = %s' % (key, value))
    scenarioINI = '\n'.join(scenarioINI)

    logging.debug('Scenario INI:')
    logging.debug(scenarioINI)

    # Write scenario
    scenario_filename = os.path.join(pred_root, 'scenario.ini')
    scenario_file = open(scenario_filename, 'w')
    scenario_file.write(scenarioINI + '\n')
    scenario_file.close()

    scenario_json_filename = os.path.join(pred_root, 'scenario.json')
    scenario_json_file = open(scenario_json_filename, 'w')
    json.dump(scenario, scenario_json_file)
    scenario_json_file.close()

    logging.debug('Launching prediction application...')

    # Try to wire everything up
    output_filename = os.path.join(pred_root, 'output.csv')
    output_file = open(output_filename, 'w')
    logging_filename = os.path.join(pred_root, 'log.txt')
    logging_file = open(logging_filename, 'w')
    pred_process = subprocess.Popen( \
        (pred_binary, '-v', '-i' + dataset_filename, '-s' + str(dataset_time),
            scenario_filename),
        stdout=output_file, stderr=logging_file)
    pred_process.wait()
    if pred_process.returncode:
        # catch the race between someone re-running a scenario and the download daemon swapping in a new dataset
        if not os.path.exists(dataset_filename):
            raise DatasetRaceError
        logging.info('Prediction process %s returned error code: %s.' % (pred_uuid, pred_process.returncode))
        # raise RuntimeError('Prediction process %s returned error code: %s.' % (pred_uuid, pred_process.returncode))
    output_file.close()
    logging_file.close()

    # Find the last line of the output
    last_output = last_line_of(output_filename)
    logging.debug('Final line of output: %s', last_output)

    (final_timestamp, latitude, longitude, alt) = map(lambda x: float(x), last_output.split(','))
    final_timestamp = int(final_timestamp)

    logging.debug('Parsed as ts=%s, lat=%s, lon=%s, alt=%s',
                  final_timestamp, latitude, longitude, alt)
    final_time = datetime.datetime.utcfromtimestamp(final_timestamp)

    manifest_entry = {
        'landing-location': {
            'latitude': latitude,
            'longitude': longitude,
            'altitude': alt,
        },
        'landing-time': {
            'year': final_time.year,
            'month': final_time.month,
            'day': final_time.day,
            'hour': final_time.hour,
            'minute': final_time.minute,
            'second': final_time.second,
        },
        'launch-time': {
            'year': prediction_time.year,
            'month': prediction_time.month,
            'day': prediction_time.day,
            'hour': prediction_time.hour,
            'minute': prediction_time.minute,
            'second': prediction_time.second,
        },
    }

    return (str(pred_uuid), manifest_entry)

def run_scenario(scenario_template, pred_root, dataset_filename, dataset_time, root):
    os.mkdir(pred_root)
    index = os.path.join(pred_root, "index.html")
    os.symlink("../lib/predicting.html", index)
    with open(os.path.join(pred_root, ".gitignore"), "w") as f:
        f.write("*")

    # Where do we store the manifest file?
    manifest_filename = os.path.join(pred_root, 'manifest.json')
    manifest = {}

    manifest['scenario-template'] = scenario_template
    manifest['model'] = os.path.basename(dataset_filename)
    manifest['predictions'] = { }

    dataset_datetime = datetime.datetime.utcfromtimestamp(dataset_time)

    for i in range(24 * 7):
        predict_time = dataset_datetime + datetime.timedelta(hours=i)
        logging.debug('Running prediction %s (%s)', i, predict_time)

        (uuid, entry) = run_prediction(predict_time, pred_root, scenario_template,
                                       dataset_filename, dataset_time, root)

        # Record in manifest
        manifest['predictions'][uuid] = entry
        logging.debug('ok')

    with open(manifest_filename, 'w') as f:
        json.dump(manifest, f)

    os.unlink(index)
    os.symlink("../lib/index.html", index)

class EventHandler(pyinotify.ProcessEvent):
    @classmethod
    def run(cls, root):
        wm = pyinotify.WatchManager()
        handler = cls(root)
        handler.add_watches(wm)
        pyinotify.Notifier(wm, handler).loop()

    def __init__(self, root):
        pyinotify.ProcessEvent.__init__(self)

        self.root = os.path.realpath(root)
        self.datasets = "/srv/tawhiri-datasets"
        self.scenarios = os.path.join(root, "hourly", "scenarios")

        if not os.path.exists(self.scenarios):
            raise ValueError("scenarios directory does not exist")
        if not os.path.exists(self.datasets):
            raise ValueError("datasets directory does not exist")

        choices = os.listdir(self.datasets)
        for choice in sorted(choices, reverse=True):  # reverse alphabetical will give newest first
            try:
                self.dataset_parse_time(choice)
            except ValueError:
                pass
            else:
                self.latest_dataset = os.path.join(self.datasets, choice)
                logging.debug("initial dataset %s; running all", choice)
                self.rerun_all()
                break
        else:
            logging.debug("no initial dataset")
            self.latest_dataset = None

    def add_watches(self, wm):
        mask = pyinotify.IN_CLOSE_WRITE | pyinotify.IN_MOVED_TO | \
               pyinotify.IN_DELETE | pyinotify.IN_MOVED_FROM
        wm.add_watch(self.scenarios, mask)
        wm.add_watch(self.datasets, mask)

    def dataset_parse_time(self, filename):
        if len(filename) != 10:
            raise ValueError
        return calendar.timegm(time.strptime(filename, "%Y%m%d%H"))

    def latest_dataset_time(self):
        if self.latest_dataset:
            return self.dataset_parse_time(os.path.basename(self.latest_dataset))
        else:
            return None

    def scenario_name(self, filename):
        if not filename.endswith(".json"):
            raise ValueError("filename does not end in .json")

        name = filename[:-5]
        if name in ('', 'scenarios', 'lib', 'edit', 'static') or '.' in name:
            raise ValueError("illegal scenario name")

        return name

    def rerun_all(self):
        assert self.latest_dataset
        for filename in os.listdir(self.scenarios):
            if filename == ".gitignore":
                continue
            try:
                name = self.scenario_name(filename)
            except ValueError as e:
                logging.warning("bad scenario filename (when running all) - %s: %s", str(e), filename)
            else:
                logging.info("running %s", name)
                self.run_scenario(name)

    def run_scenario(self, name):
        if not self.latest_dataset:
            logging.warning("not running scenario %s - no dataset", name)
            return

        if not os.path.exists(self.latest_dataset):
            logging.warning("Dataset race error pre-scenario %s, not running, expecting retry", name)
            return

        scenario_file = os.path.join(self.scenarios, name + ".json")
        scenario_data_directory = os.path.join(self.root, "hourly", "web", name)

        self.clean_scenario(name)

        try:
            with open(scenario_file) as f:
                scenario_template = json.load(f)
        except ValueError:
            logging.error("bad scenario JSON: %s", name)
            return

        del scenario_template["password"]

        args = (scenario_template, scenario_data_directory,
                self.latest_dataset, self.latest_dataset_time(),
                self.root)

        try:
            logging.debug("run_scenario(%r, %r, %r, %r, %r)", *args)
            run_scenario(*args)
        except DatasetRaceError:
            logging.warning("Dataset race error mid-scenario %s, cleaning up and expecting retry", name)
            self.clean_scenario(name)
        except Exception:
            logging.exception("scenario run failed: %s", name)
            self.clean_scenario(name)
        else:
            logging.info("scenario run complete: %s", name)

    def clean_scenario(self, name):
        scenario_data_directory = os.path.join(self.root, "hourly", "web", name)
        logging.debug("cleaning scenario %s", name)
        if os.path.exists(scenario_data_directory):
            shutil.rmtree(scenario_data_directory)

    def process_scenario_changed(self, event):
        directory, filename = os.path.split(event.pathname)
        assert directory == self.scenarios
        try:
            name = self.scenario_name(filename)
        except ValueError as e:
            logging.debug("%s: %s", str(e), filename)
        else:
            logging.info("Scenario %s modified: re-running", name)
            self.run_scenario(name)

    def process_scenario_deleted(self, event):
        directory, filename = os.path.split(event.pathname)
        assert directory == self.scenarios
        try:
            name = self.scenario_name(filename)
        except ValueError as e:
            logging.debug("%s: %s", str(e), filename)
        else:
            logging.info("Scenario %s removed: cleaning", name)
            self.clean_scenario(name)

    def process_dataset_added(self, event):
        directory, filename = os.path.split(event.pathname)
        assert directory == self.datasets
        try:
            new_time = self.dataset_parse_time(filename)
        except ValueError:
            logging.debug("file added was not a dataset: %s", filename)
        else:
            # This used to be new_time >= self.latest_dataset_time().
            # However, this could produce duplicate new-dataset-added events,
            # which are quite expensive for us. In particular, the dataset is
            # un-mapped from the downloader process whenever the downloader
            # process' garbage collector feels like it, and that produces a
            # CLOSE_WRITE event. We really only care about the MOVED_TO event
            # that reliably happens upon download completion.
            if new_time > self.latest_dataset_time():
                logging.info("new dataset added: %s; re-running all", filename)
                self.latest_dataset = event.pathname
                self.rerun_all()
            else:
                logging.warning("dataset added was not newer")

    def process_dataset_deleted(self, event):
        directory, filename = os.path.split(event.pathname)
        assert directory == self.datasets
        if event.pathname == self.latest_dataset:
            logging.warning("latest dataset was deleted")
            self.latest_dataset = None
        else:
            try:
                self.dataset_parse_time(filename)
            except ValueError:
                logging.debug("unrelated dataset file deleted: %s", filename)
            else:
                logging.debug("older dataset file deleted: %s", filename)

    def process_IN_CLOSE_WRITE(self, event):
        logging.debug("CLOSE WRITE: %s", event.pathname)
        if event.pathname.startswith(self.scenarios):
            self.process_scenario_changed(event)
        else:
            self.process_dataset_added(event)

    def process_IN_MOVED_TO(self, event):
        logging.debug("MOVED TO: %s", event.pathname)
        if event.pathname.startswith(self.scenarios):
            self.process_scenario_changed(event)
        else:
            self.process_dataset_added(event)

    def process_IN_DELETE(self, event):
        logging.debug("DELETE: %s", event.pathname)
        if event.pathname.startswith(self.scenarios):
            self.process_scenario_deleted(event)
        else:
            self.process_dataset_deleted(event)

    def process_IN_MOVED_FROM(self, event):
        logging.debug("MOVED_FROM: %s", event.pathname)
        if event.pathname.startswith(self.scenarios):
            self.process_scenario_deleted(event)
        else:
            self.process_dataset_deleted(event)

_format_email = \
"""%(levelname)s from logger %(name)s (thread %(threadName)s)

Time:       %(asctime)s
Location:   %(pathname)s:%(lineno)d
Module:     %(module)s
Function:   %(funcName)s

%(message)s"""

_format_string = \
"[%(asctime)s] %(levelname)s %(name)s %(threadName)s: %(message)s"

if __name__ == '__main__':
    logging.getLogger().setLevel(logging.DEBUG)

    handler = logging.handlers.SMTPHandler(
            "localhost", "hourly@localhost", "daniel@localhost",
            "hourly predictor daemon")
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter(_format_email))
    logging.getLogger().addHandler(handler)

    handler = logging.StreamHandler() # stderr
    handler.setFormatter(logging.Formatter(_format_string))
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    try:
        EventHandler.run(root)
    except Exception:
        logging.exception("unhandled exception")
        raise

# vim:sw=4:ts=4:et:autoindent
