<?php

// Enter the Google Maps API for your site
define("GMAPS_API_KEY", "AIzaSyD5mW6bdyvChDy-WM_EUI5_sLBvIxidJbE");

// Who should we email about errors etc?
define("ADMIN_EMAIL", "adam@adamgreig.com");

define("LOCATION_SAVE_ENABLE", true);

// Path to the root of the git repo inc. trailing /
define("ROOT", "/var/www/predict/");

// Path to python virtualenv to use
define("PYTHON", ROOT . "venv/bin/python");

// Path to prediction data dir from predict/
define("PREDS_PATH", "preds/");

// Path to the dataset directory from ROOT
define("DATASET_DIR", "/srv/tawhiri-datasets");

// Filenames used by the predictor
define("SCENARIO_FILE", "scenario.ini");
define("FLIGHT_CSV", "flight_path.csv");
define("PROGRESS_JSON", "progress.json");
define("LOG_FILE", "py_log");

?>
