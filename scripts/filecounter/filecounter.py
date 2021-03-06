import os, thread, json, collections
import logging, logging.config, logstash
import time
import pandas as pd
import numpy as np
import datetime
import psycopg2
import re
from flask import Flask, render_template, send_file, request, url_for, redirect, make_response
from flask_wtf import FlaskForm as Form
from wtforms import TextField, TextAreaField, validators, StringField, SubmitField, DateField
from wtforms.fields.html5 import DateField
from wtforms.validators import DataRequired
import counts


config = {}
app_dir = '/home/filecounter'
SCAN_LOCK = False
count_defs = counts.SENSOR_COUNT_DEFINITIONS
DEFAULT_COUNT_START = None
DEFAULT_COUNT_END = None

# UTILITIES ----------------------------
def loadJsonFile(filename):
    """Load contents of .json file into a JSON object"""
    try:
        f = open(filename)
        jsonObj = json.load(f)
        f.close()
        return jsonObj
    except IOError:
        logger.error("- unable to open %s" % filename)
        return {}

def updateNestedDict(existing, new):
    """Nested update of python dictionaries for config parsing
    Adapted from http://stackoverflow.com/questions/3232943/update-value-of-a-nested-dictionary-of-varying-depth
    """
    for k, v in new.iteritems():
        if isinstance(existing, collections.Mapping):
            if isinstance(v, collections.Mapping):
                r = updateNestedDict(existing.get(k, {}), v)
                existing[k] = r
            else:
                existing[k] = new[k]
        else:
            existing = {k: new[k]}
    return existing

def generate_dates_in_range(start_date_string, end_date_string=None):
    """Return list of date strings between start and end dates."""
    start_date = datetime.datetime.strptime(start_date_string, '%Y-%m-%d')
    if not end_date_string:
        end_date = datetime.datetime.now()
    else:
        end_date = datetime.datetime.strptime(end_date_string, '%Y-%m-%d')
    days_between = (end_date - start_date).days

    date_strings = []
    for i in range(0, days_between+1):
        current_date = start_date + datetime.timedelta(days=i)
        current_date_string = current_date.strftime('%Y-%m-%d')
        date_strings.append(current_date_string)
    return date_strings


def get_percent_columns(current_dataframe):
    colnames = list(current_dataframe.columns.values)
    percent_columns = []
    for each in colnames:
        if each.endswith('%'):
            percent_columns.append(each)
    return percent_columns


def highlight_max(s):
    '''
    highlight the maximum in a Series yellow.
    '''
    is_max = s == s.max()
    return ['background-color: red' if v else '' for v in is_max]


def color_percents(val):
    """
    Takes a scalar and returns a string with
    the css property `'color: red'` for negative
    strings, black otherwise.
    """
    if val == 100:
        color = 'green'
    elif 100 > val >= 99:
        color = 'orange'
    elif val < 99:
        color = 'yellow'
    else:
        color = 'yellow'
    return 'background-color: %s' % color


# FLASK COMPONENTS ----------------------------
def create_app(test_config=None):

    pipeline_csv = os.path.join(config['csv_path'], "{}.csv")

    sensor_names = count_defs.keys()

    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY='dev',
        DATABASE=os.path.join(app.instance_path, 'flaskr.sqlite'),
    )

    if test_config is None:
        # load the instance config, if it exists, when not testing
        app.config.from_pyfile('config.py', silent=True)
    else:
        # load the test config if passed in
        app.config.from_mapping(test_config)

    # ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    class ExampleForm(Form):
        start_date = DateField('Start', format='%Y-%m-%d', validators=[DataRequired()])
        end_date = DateField('End', format='%Y-%m-%d')
        submit = SubmitField('Count files for these days',validators=[DataRequired()])

    @app.route('/sensors', defaults={'message': "Available Sensors and Options"})
    @app.route('/sensors/<string:message>')
    def sensors(message):
        return render_template('sensors.html', sensors=sensor_names, message=message)

    @app.route('/test')
    def test():
        return 'this is only a test, route does nothing'

    @app.route('/download/<sensor_name>')
    def download(sensor_name):
        current_csv = pipeline_csv.format(sensor_name)
        current_csv_name = os.path.basename(current_csv)
        return send_file(current_csv,
                         mimetype='text/csv',
                         attachment_filename=current_csv_name,
                         as_attachment=True)

    @app.route('/showcsv/<sensor_name>', defaults={'days': 14})
    @app.route('/showcsv/<sensor_name>/<int:days>')
    def showcsv(sensor_name, days):
        # data = dataset.html
        current_csv = pipeline_csv.format(sensor_name)
        if not os.path.isfile(current_csv):
            return "File does not exist"
        df = pd.read_csv(current_csv, index_col=False)
        if days == 0:
            percent_columns = get_percent_columns(df)
            for each in percent_columns:
                df[each] = df[each].mul(100).astype(int)
            dfs = df.style
            dfs.applymap(color_percents, subset=percent_columns).set_table_attributes("border=1")
            my_html = dfs.render()
            return my_html
        else:
            return df.tail(days).to_html()

    @app.route('/showcsvbyseason/<sensor_name>', defaults={'season': 6})
    @app.route('/showcsv/<sensor_name>/<int:season>')
    def showcsvbyseason(sensor_name, season):
        if season == 6:
            start = '2018-04-06'
            end = '2018-08-01'
            current_csv = pipeline_csv.format(sensor_name)
            df = pd.read_csv(current_csv, index_col=False)
            df_season = df.loc[(df['date'] >= start) & (df['date'] <= end)]
            if 'stereoTop' in df_season.columns:
                df_season = df_season[df['stereoTop'] != 0]
            if 'flirIrCamera' in df_season.columns:
                df_season = df_season[df['flirIrCamera'] != 0]
            if 'scanner3DTop' in df_season.columns:
                df_season = df_season[df['scanner3DTop'] != 0]
            percent_columns = get_percent_columns(df_season)
            for each in percent_columns:
                df_season[each] = df_season[each].mul(100).astype(int)
            dfs = df_season.style
            dfs.applymap(color_percents, subset=percent_columns).set_table_attributes("border=1")
            my_html = dfs.render()
            return my_html
        else:
            current_csv = pipeline_csv.format(sensor_name)
            df = pd.read_csv(current_csv, index_col=False)
            percent_columns = get_percent_columns(df)
            for each in percent_columns:
                df[each] = df[each].mul(100).astype(int)
            dfs = df.style
            dfs.applymap(color_percents, subset=percent_columns).set_table_attributes("border=1")
            my_html = dfs.render()
            return my_html

    @app.route('/testcsv')
    def testcsv():
        current_csv ='stereoTop.csv'
        df = pd.read_csv(current_csv, index_col=False)
        percent_columns = get_percent_columns(df)
        for each in percent_columns:
            df[each] = df[each].mul(100).astype(int)
        dfs = df.style
        dfs.applymap(color_percents, subset=percent_columns).set_table_attributes("border=1")
        my_html = dfs.render()
        return my_html

    @app.route('/testcsv2')
    def testcsv2():
        np.random.seed(24)
        df = pd.DataFrame({'A': np.linspace(1, 10, 10)})
        df = pd.concat([df, pd.DataFrame(np.random.randn(10, 4), columns=list('BCDE'))],
               axis=1)
        df.iloc[0, 2] = np.nan
        df.style.apply(highlight_max)
        return df.to_html()

    @app.route('/dateoptions', methods=['POST','GET'])
    def dateoptions():
        form = ExampleForm(request.form)
        if form.validate_on_submit():
            return redirect(url_for('schedule_count',
                                    sensor_name='all',
                                    start_range=str(form.start_date.data.strftime('%Y-%m-%d')),
                                    end_range=str(form.end_date.data.strftime('%Y-%m-%d'))))
        return render_template('dateoptions.html', form=form)

    @app.route('/archive')
    def archive():
        sensor_list = count_defs.keys()
        current_time_stamp = str(datetime.datetime.now()).replace(' ', '_')
        for sensor in sensor_list:
            output_file = os.path.join(config['csv_path'], sensor + ".csv")
            if os.path.exists(output_file):
                archived_file = os.path.join(config['csv_path'], sensor + '_' + current_time_stamp + ".csv")
                os.rename(output_file, archived_file)
                if os.path.exists(output_file):
                    try:
                        os.remove(output_file)
                    except OSError as e:
                        logging.info(e)
        message = "Archived existing count csvs"
        logging.info("Archived existing count csvs")
        return redirect(url_for('sensors', message=message))

    @app.route('/schedule/<sensor_name>/<start_range>', defaults={'end_range': None})
    @app.route('/schedule/<sensor_name>/<start_range>/<end_range>')
    def schedule_count(sensor_name, start_range, end_range):
        if sensor_name.lower() == "all":
            sensor_list = count_defs.keys()
        else:
            sensor_list = [sensor_name]

        dates_in_range = generate_dates_in_range(start_range, end_range)

        psql_db = os.getenv("RULECHECKER_DATABASE", config['postgres']['database'])
        psql_host = os.getenv("RULECHECKER_HOST", config['postgres']['host'])
        psql_user = os.getenv("RULECHECKER_USER", config['postgres']['username'])
        psql_pass = os.getenv("RULECHECKER_PASSWORD", config['postgres']['password'])

        conn = psycopg2.connect(dbname=psql_db, user=psql_user, host=psql_host, password=psql_pass)

        thread.start_new_thread(update_file_count_csvs, (sensor_list, dates_in_range, conn))

        message = "Custom scan scheduled for %s sensors and %s dates" % (len(sensor_list), len(dates_in_range))
        return redirect(url_for('sensors', message=message))

    return app


# COUNTING COMPONENTS ----------------------------
def run_regular_update(use_defaults=False):
    """Perform regular update of previous two weeks for all sensors"""
    psql_db = os.getenv("RULECHECKER_DATABASE", config['postgres']['database'])
    psql_host = os.getenv("RULECHECKER_HOST", config['postgres']['host'])
    psql_user = os.getenv("RULECHECKER_USER", config['postgres']['username'])
    psql_pass = os.getenv("RULECHECKER_PASSWORD", config['postgres']['password'])

    conn = psycopg2.connect(dbname=psql_db, user=psql_user, host=psql_host, password=psql_pass)

    while True:
        # Determine two weeks before current date, or by defaults
        if use_defaults:
            logging.info("Using default values instead of previous 2 weeks")
            start_date_string = DEFAULT_COUNT_START
            end_date_string = DEFAULT_COUNT_END
            dates_to_check = generate_dates_in_range(start_date_string, end_date_string)
        else:
            today = datetime.datetime.now()
            two_weeks = today - datetime.timedelta(days=14)
            start_date_string = os.getenv('START_SCAN_DATE', two_weeks.strftime("%Y-%m-%d"))
            dates_to_check = generate_dates_in_range(start_date_string)

        logging.info("Checking counts for dates %s - %s" % (start_date_string, dates_to_check[-1]))

        update_file_count_csvs(count_defs.keys(), dates_to_check, conn)

        # Wait 1 hour for next iteration
        time.sleep(3600)

def retrive_single_count(target_count, target_def, date, conn):
    """Return count of specified type (see counts.py for types)"""
    count = 0

    if target_def["type"] == "timestamp":
        date_dir = os.path.join(target_def["path"], date)
        if os.path.exists(date_dir):
            logging.info("   [%s] counting timestamps in %s" % (target_count, date_dir))
            count = len(os.listdir(date_dir))
        else:
            logging.info("   [%s] directory not found: %s" % (target_count, date_dir))

    elif target_def["type"] == "plot":
        date_dir = os.path.join(target_def["path"], date)
        if os.path.exists(date_dir):
            logging.info("   [%s] counting plots in %s" % (target_count, date_dir))
            count = len(os.listdir(date_dir))
        else:
            logging.info("   [%s] directory not found: %s" % (target_count, date_dir))

    elif target_def["type"] == "regex":
        date_dir = os.path.join(target_def["path"], date)
        if os.path.exists(date_dir):
            logging.info("   [%s] matching regex against %s" % (target_count, date_dir))
            for date_content in os.listdir(date_dir):
                if os.path.isdir(date_content):
                    # This is timestamp-level search
                    ts_dir = os.path.join(date_dir, date_content)
                    for file in os.listdir(ts_dir):
                        if re.match(target_def["regex"], file):
                            count += 1
                else:
                    # No timestamp (e.g. fullfield)
                    if re.match(target_def["regex"], date_content):
                        count += 1
        else:
            logging.info("   [%s] directory not found: %s" % (target_count, date_dir))

    elif target_def["type"] == "psql":
        logging.info("   [%s] querying PSQL records for %s" % (target_count, date))
        query_string = target_def["query"] % date
        curs = conn.cursor()
        curs.execute(query_string)
        for result in curs:
            count = result[0]

    return count

def update_file_count_csvs(sensor_list, dates_to_check, conn):
    """Perform necessary counting on specified dates to update CSV for all sensors."""
    global SCAN_LOCK

    while SCAN_LOCK:
        logging.info("Another thread currently locking database; waiting 60 seconds to retry")
        time.sleep(60)

    logging.info("Locking scan for %s sensors and %s dates" % (len(sensor_list), len(dates_to_check)))
    SCAN_LOCK = True
    for sensor in sensor_list:
        output_file = os.path.join(config['csv_path'], sensor+".csv")
        logging.info("Updating counts for %s into %s" % (sensor, output_file))
        targets = count_defs[sensor]
        cols = ["date"]
        for target_count in targets:
            target_def = targets[target_count]
            cols.append(target_count)
            if "parent" in target_def:
                cols.append(target_count + '%')

        # Load data frame from existing CSV or create a new one
        if os.path.exists(output_file):
            logging.info("csv exists for %s" % output_file)
            try:
                df = pd.read_csv(output_file)
            except Exception as e:
                logging.info(e)
                logging.info('CSV exists, could not read as dataframe')
                cols = ["date"]
                df = pd.DataFrame(columns=cols)
                logging.info("CSV existed but could not be read, created dataframe for %s " % sensor)
            df_columns = list(df.columns.values)
            if df_columns != cols:
                logging.info("CSV existed but had malformed columns, created dataframe for %s " % sensor)
                df = pd.DataFrame(columns=cols)
        else:
            logging.info("output file for %s does not exist" % sensor)
            # cols = ["date"]
            # for target_count in targets:
            #     target_def = targets[target_count]
            #     cols.append(target_count)
            #     if "parent" in target_def:
            #         cols.append(target_count+'%')
            df = pd.DataFrame(columns=cols)
            logging.info("CSV did not exist, created dataframe for %s " % sensor)

        # Populate count and percentage (if applicable) for each target count
        logging.info("the columns of the csv are %s " % str(df.columns.values))
        for current_date in dates_to_check:
            logging.info("[%s] %s" % (sensor, current_date))
            counts = {}
            percentages = {}
            for target_count in targets:
                target_def = targets[target_count]
                counts[target_count] = retrive_single_count(target_count, target_def, current_date, conn)
                if "parent" in target_def:
                    if target_def["parent"] not in counts:
                        counts[target_def["parent"]] = retrive_single_count(targets[target_def["parent"]], current_date, conn)
                    if counts[target_def["parent"]] > 0:
                        percentages[target_count] = (counts[target_count]*1.0)/(counts[target_def["parent"]]*1.0)
                    else:
                        percentages[target_count] = 0.0

            # If this date already has a row, just update
            if current_date in df['date'].values:
                logging.info("Already have data for date %s " % current_date)
                updated_entry = [current_date]
                for target_count in targets:
                    target_def = targets[target_count]
                    updated_entry.append(counts[target_count])
                    if "parent" in target_def:
                        updated_entry.append(percentages[target_count])
                df.loc[df['date'] == current_date] = updated_entry
            # If not, create a new row
            else:
                logging.info("No data for date %s adding to dataframe" % current_date)
                new_entry = [current_date]
                indices = ["date"]

                for target_count in targets:
                    target_def = targets[target_count]

                    indices.append(target_count)
                    new_entry.append(counts[target_count])
                    if "parent" in target_def:
                        indices.append(target_count+'%')
                        new_entry.append(percentages[target_count])
                df = df.append(pd.Series(new_entry, index=indices), ignore_index=True)

        logging.info("Writing %s" % output_file)
        df['date'] = pd.to_datetime(df['date'], format='%Y-%m-%d')
        df.sort_values(by=['date'], inplace=True, ascending=True)
        df.to_csv(output_file, index=False)

    SCAN_LOCK = False


if __name__ == '__main__':

    logger = logging.getLogger('counter')

    config = loadJsonFile(os.path.join(app_dir, "config_default.json"))
    if os.path.exists(os.path.join(app_dir, "data/config_custom.json")):
        print("...loading configuration from config_custom.json")
        config = updateNestedDict(config, loadJsonFile(os.path.join(app_dir, "data/config_custom.json")))
        try:
            DEFAULT_COUNT_START = str(config["default_count_start"])
            DEFAULT_COUNT_END = str(config["default_count_end"])
            print(DEFAULT_COUNT_START, DEFAULT_COUNT_END)
            print("default start and end provided")
        except:
            print("No default values for start and end")
    else:
        print("...no custom configuration file found. using default values")

    # Initialize logger handlers
    with open(os.path.join(app_dir, "config_logging.json"), 'r') as f:
        log_config = json.load(f)
        main_log_file = os.path.join(config["log_path"], "log_filecounter.txt")
        log_config['handlers']['file']['filename'] = main_log_file
        if not os.path.exists(config["log_path"]):
            os.makedirs(config["log_path"])
        if not os.path.isfile(main_log_file):
            open(main_log_file, 'a').close()
        logging.config.dictConfig(log_config)

    thread.start_new_thread(run_regular_update, (True,))

    apiIP = os.getenv('COUNTER_API_IP', "0.0.0.0")
    apiPort = os.getenv('COUNTER_API_PORT', "5454")
    app = create_app()
    logger.info("*** API now listening on %s:%s ***" % (apiIP, apiPort))
    app.run(host=apiIP, port=apiPort)
