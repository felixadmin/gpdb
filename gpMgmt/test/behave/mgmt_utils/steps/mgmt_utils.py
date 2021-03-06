import fnmatch
import getpass
import glob
import json
import yaml
import os
import re
import platform
import shutil
import socket
import tarfile
import tempfile
import thread
import json
import subprocess
import commands
import signal
from collections import defaultdict

from behave import given, when, then
from datetime import datetime
from time import sleep


from gppylib.commands.gp import SegmentStart, GpStandbyStart, MasterStop
from gppylib.commands.unix import findCmdInPath, Scp
from gppylib.operations.startSegments import MIRROR_MODE_MIRRORLESS
from gppylib.operations.unix import ListRemoteFilesByPattern, CheckRemoteFile
from test.behave_utils.gpfdist_utils.gpfdist_mgmt import Gpfdist
from test.behave_utils.utils import *
from test.behave_utils.cluster_setup import TestCluster, reset_hosts
from test.behave_utils.cluster_expand import Gpexpand
from gppylib.commands.base import Command, REMOTE


master_data_dir = os.environ.get('MASTER_DATA_DIRECTORY')
if master_data_dir is None:
    raise Exception('Please set MASTER_DATA_DIRECTORY in environment')


@given('the cluster config is generated with data_checksums "{checksum_toggle}"')
def impl(context, checksum_toggle):
    stop_database(context)

    cmd = """
    cd ../gpAux/gpdemo; \
        export MASTER_DEMO_PORT={master_port} && \
        export DEMO_PORT_BASE={port_base} && \
        export NUM_PRIMARY_MIRROR_PAIRS={num_primary_mirror_pairs} && \
        export WITH_MIRRORS={with_mirrors} && \
        ./demo_cluster.sh -d && ./demo_cluster.sh -c && \
        env EXTRA_CONFIG="HEAP_CHECKSUM={checksum_toggle}" ONLY_PREPARE_CLUSTER_ENV=true ./demo_cluster.sh
    """.format(master_port=os.getenv('MASTER_PORT', 15432),
               port_base=os.getenv('PORT_BASE', 25432),
               num_primary_mirror_pairs=os.getenv('NUM_PRIMARY_MIRROR_PAIRS', 3),
               with_mirrors='true',
               checksum_toggle=checksum_toggle)

    run_command(context, cmd)

    if context.ret_code != 0:
        raise Exception('%s' % context.error_message)


@given('the database is running')
@then('the database is running')
def impl(context):
    start_database_if_not_started(context)
    if has_exception(context):
        raise context.exception


@given('the database is initialized with checksum "{checksum_toggle}"')
def impl(context, checksum_toggle):
    is_ok = check_database_is_running(context)

    if is_ok:
        run_command(context, "gpconfig -s data_checksums")
        if context.ret_code != 0:
            raise Exception("cannot run gpconfig: %s, stdout: %s" % (context.error_message, context.stdout_message))

        try:
            # will throw
            check_stdout_msg(context, "Values on all segments are consistent")
            check_stdout_msg(context, "Master  value: %s" % checksum_toggle)
            check_stdout_msg(context, "Segment value: %s" % checksum_toggle)
        except:
            is_ok = False

    if not is_ok:
        stop_database(context)

        master_port = os.getenv('PGPORT', 15432)
        port_base = str(int(master_port) + 10000)

        cmd = """
        cd ../gpAux/gpdemo; \
            export MASTER_DEMO_PORT={master_port} && \
            export DEMO_PORT_BASE={port_base} && \
            export NUM_PRIMARY_MIRROR_PAIRS={num_primary_mirror_pairs} && \
            export WITH_MIRRORS={with_mirrors} && \
            ./demo_cluster.sh -d && ./demo_cluster.sh -c && \
            env EXTRA_CONFIG="HEAP_CHECKSUM={checksum_toggle}" ./demo_cluster.sh
        """.format(master_port=master_port,
                   port_base=port_base,
                   num_primary_mirror_pairs=os.getenv('NUM_PRIMARY_MIRROR_PAIRS', 3),
                   with_mirrors='true',
                   checksum_toggle=checksum_toggle)

        run_command(context, cmd)

        if context.ret_code != 0:
            raise Exception('%s' % context.error_message)

        if ('PGDATABASE' in os.environ):
            run_command(context, "createdb %s" % os.getenv('PGDATABASE'))

@given('the database is not running')
@when('the database is not running')
def impl(context):
    stop_database_if_started(context)
    if has_exception(context):
        raise context.exception


@given('database "{dbname}" exists')
@then('database "{dbname}" exists')
def impl(context, dbname):
    create_database_if_not_exists(context, dbname)


@given('database "{dbname}" is created if not exists on host "{HOST}" with port "{PORT}" with user "{USER}"')
@then('database "{dbname}" is created if not exists on host "{HOST}" with port "{PORT}" with user "{USER}"')
def impl(context, dbname, HOST, PORT, USER):
    host = os.environ.get(HOST)
    port = 0 if os.environ.get(PORT) == None else int(os.environ.get(PORT))
    user = os.environ.get(USER)
    create_database_if_not_exists(context, dbname, host, port, user)


@when('the database "{dbname}" does not exist')
@given('the database "{dbname}" does not exist')
@then('the database "{dbname}" does not exist')
def impl(context, dbname):
    drop_database_if_exists(context, dbname)


@when('the database "{dbname}" does not exist on host "{HOST}" with port "{PORT}" with user "{USER}"')
@given('the database "{dbname}" does not exist on host "{HOST}" with port "{PORT}" with user "{USER}"')
@then('the database "{dbname}" does not exist on host "{HOST}" with port "{PORT}" with user "{USER}"')
def impl(context, dbname, HOST, PORT, USER):
    host = os.environ.get(HOST)
    port = int(os.environ.get(PORT))
    user = os.environ.get(USER)
    drop_database_if_exists(context, dbname, host, port, user)


def get_segment_hostlist():
    gparray = GpArray.initFromCatalog(dbconn.DbURL())
    segment_hostlist = sorted(gparray.get_hostlist(includeMaster=False))
    if not segment_hostlist:
        raise Exception('segment_hostlist was empty')
    return segment_hostlist


@given('the user truncates "{table_list}" tables in "{dbname}"')
@when('the user truncates "{table_list}" tables in "{dbname}"')
@then('the user truncates "{table_list}" tables in "{dbname}"')
def impl(context, table_list, dbname):
    if not table_list:
        raise Exception('Table list is empty')
    tables = table_list.split(',')
    for t in tables:
        truncate_table(dbname, t.strip())


@given(
    'there is a partition table "{tablename}" has external partitions of gpfdist with file "{filename}" on port "{port}" in "{dbname}" with data')
def impl(context, tablename, dbname, filename, port):
    create_database_if_not_exists(context, dbname)
    drop_table_if_exists(context, table_name=tablename, dbname=dbname)
    create_external_partition(context, tablename, dbname, port, filename)


@given('"{dbname}" does not exist')
def impl(context, dbname):
    drop_database(context, dbname)


@given('{env_var} environment variable is not set')
def impl(context, env_var):
    if not hasattr(context, 'orig_env'):
        context.orig_env = dict()
    context.orig_env[env_var] = os.environ.get(env_var)

    if env_var in os.environ:
        del os.environ[env_var]


@then('{env_var} environment variable should be restored')
def impl(context, env_var):
    if not hasattr(context, 'orig_env'):
        raise Exception('%s can not be reset' % env_var)

    if env_var not in context.orig_env:
        raise Exception('%s can not be reset.' % env_var)

    os.environ[env_var] = context.orig_env[env_var]

    del context.orig_env[env_var]


@given('the user runs "{command}"')
@when('the user runs "{command}"')
@then('the user runs "{command}"')
def impl(context, command):
    run_gpcommand(context, command)


@given('the user asynchronously runs "{command}" and the process is saved')
@when('the user asynchronously runs "{command}" and the process is saved')
@then('the user asynchronously runs "{command}" and the process is saved')
def impl(context, command):
    run_gpcommand_async(context, command)


@given('the async process finished with a return code of {ret_code}')
@when('the async process finished with a return code of {ret_code}')
@then('the async process finished with a return code of {ret_code}')
def impl(context, ret_code):
    rc, stdout_value, stderr_value = context.asyncproc.communicate2()
    if rc != int(ret_code):
        raise Exception("return code of the async proccess didn't match:\n"
                        "rc: %s\n"
                        "stdout: %s\n"
                        "stderr: %s" % (rc, stdout_value, stderr_value))


@given('a user runs "{command}" with gphome "{gphome}"')
@when('a user runs "{command}" with gphome "{gphome}"')
@then('a user runs "{command}" with gphome "{gphome}"')
def impl(context, command, gphome):
    masterhost = get_master_hostname()[0][0]
    cmd = Command(name='Remove archive gppkg',
                  cmdStr=command,
                  ctxt=REMOTE,
                  remoteHost=masterhost,
                  gphome=gphome)
    cmd.run()
    context.ret_code = cmd.get_return_code()


@given('the user runs command "{command}"')
@when('the user runs command "{command}"')
@then('the user runs command "{command}"')
def impl(context, command):
    run_command(context, command)


@when('the user runs async command "{command}"')
def impl(context, command):
    run_async_command(context, command)


@given('the user runs workload under "{dir}" with connection "{dbconn}"')
@when('the user runs workload under "{dir}" with connection "{dbconn}"')
def impl(context, dir, dbconn):
    for file in os.listdir(dir):
        if file.endswith('.sql'):
            command = '%s -f %s' % (dbconn, os.path.join(dir, file))
            run_command(context, command)


@given('the user modifies the external_table.sql file "{filepath}" with host "{HOST}" and port "{port}"')
@when('the user modifies the external_table.sql file "{filepath}" with host "{HOST}" and port "{port}"')
def impl(context, filepath, HOST, port):
    host = os.environ.get(HOST)
    substr = host + ':' + port
    modify_sql_file(filepath, substr)


@given('the user starts the gpfdist on host "{HOST}" and port "{port}" in work directory "{dir}" from remote "{ctxt}"')
@then('the user starts the gpfdist on host "{HOST}" and port "{port}" in work directory "{dir}" from remote "{ctxt}"')
def impl(context, HOST, port, dir, ctxt):
    host = os.environ.get(HOST)
    remote_gphome = os.environ.get('GPHOME')
    if not dir.startswith("/"):
        dir = os.environ.get(dir)
    gp_source_file = os.path.join(remote_gphome, 'greenplum_path.sh')
    gpfdist = Gpfdist('gpfdist on host %s' % host, dir, port, os.path.join(dir, 'gpfdist.pid'), int(ctxt), host,
                      gp_source_file)
    gpfdist.startGpfdist()


@given('the user stops the gpfdist on host "{HOST}" and port "{port}" in work directory "{dir}" from remote "{ctxt}"')
@then('the user stops the gpfdist on host "{HOST}" and port "{port}" in work directory "{dir}" from remote "{ctxt}"')
def impl(context, HOST, port, dir, ctxt):
    host = os.environ.get(HOST)
    remote_gphome = os.environ.get('GPHOME')
    if not dir.startswith("/"):
        dir = os.environ.get(dir)
    gp_source_file = os.path.join(remote_gphome, 'greenplum_path.sh')
    gpfdist = Gpfdist('gpfdist on host %s' % host, dir, port, os.path.join(dir, 'gpfdist.pid'), int(ctxt), host,
                      gp_source_file)
    gpfdist.cleanupGpfdist()


@then('{command} should print "{err_msg}" error message')
def impl(context, command, err_msg):
    check_err_msg(context, err_msg)


@when('{command} should print "{out_msg}" to stdout')
@then('{command} should print "{out_msg}" to stdout')
def impl(context, command, out_msg):
    check_stdout_msg(context, out_msg)


@then('{command} should not print "{out_msg}" to stdout')
def impl(context, command, out_msg):
    check_string_not_present_stdout(context, out_msg)


@then('{command} should print "{out_msg}" to stdout {num} times')
def impl(context, command, out_msg, num):
    msg_list = context.stdout_message.split('\n')
    msg_list = [x.strip() for x in msg_list]

    count = msg_list.count(out_msg)
    if count != int(num):
        raise Exception("Expected %s to occur %s times. Found %d" % (out_msg, num, count))


@given('{command} should return a return code of {ret_code}')
@when('{command} should return a return code of {ret_code}')
@then('{command} should return a return code of {ret_code}')
def impl(context, command, ret_code):
    check_return_code(context, ret_code)


@given('the segments are synchronized')
@when('the segments are synchronized')
@then('the segments are synchronized')
def impl(context):
    times = 60
    sleeptime = 10

    for i in range(times):
        if are_segments_synchronized():
            return
        time.sleep(sleeptime)

    raise Exception('segments are not in sync after %d seconds' % (times * sleeptime))


@then('verify data integrity of database "{dbname}" between source and destination system, work-dir "{dirname}"')
def impl(context, dbname, dirname):
    dbconn_src = 'psql -p $GPTRANSFER_SOURCE_PORT -h $GPTRANSFER_SOURCE_HOST -U $GPTRANSFER_SOURCE_USER -d %s' % dbname
    dbconn_dest = 'psql -p $GPTRANSFER_DEST_PORT -h $GPTRANSFER_DEST_HOST -U $GPTRANSFER_DEST_USER -d %s' % dbname
    for filename in os.listdir(dirname):
        if filename.endswith('.sql'):
            filename_prefix = os.path.splitext(filename)[0]
            ans_file_path = os.path.join(dirname, filename_prefix + '.ans')
            out_file_path = os.path.join(dirname, filename_prefix + '.out')
            diff_file_path = os.path.join(dirname, filename_prefix + '.diff')
            # run the command to get the exact data from the source system
            command = '%s -f %s > %s' % (dbconn_src, os.path.join(dirname, filename), ans_file_path)
            run_command(context, command)

            # run the command to get the data from the destination system, locally
            command = '%s -f %s > %s' % (dbconn_dest, os.path.join(dirname, filename), out_file_path)
            run_command(context, command)

            gpdiff_cmd = 'gpdiff.pl -w -I NOTICE: -I HINT: -I CONTEXT: -I GP_IGNORE: --gpd_init=test/behave/mgmt_utils/steps/data/global_init_file %s %s > %s' % (
            ans_file_path, out_file_path, diff_file_path)
            run_command(context, gpdiff_cmd)
            if context.ret_code != 0:
                with open(diff_file_path, 'r') as diff_file:
                    diff_file_contents = diff_file.read()
                    raise Exception(
                        "Found difference between source and destination system, see %s. \n Diff contents: \n %s" % (
                        diff_file_path, diff_file_contents))


@then('verify that there is no table "{tablename}" in "{dbname}"')
def impl(context, tablename, dbname):
    dbname = replace_special_char_env(dbname)
    tablename = replace_special_char_env(tablename)
    if check_table_exists(context, dbname=dbname, table_name=tablename):
        raise Exception("Table '%s' still exists when it should not" % tablename)


@then('verify that there is a "{table_type}" table "{tablename}" in "{dbname}"')
def impl(context, table_type, tablename, dbname):
    if not check_table_exists(context, dbname=dbname, table_name=tablename, table_type=table_type):
        raise Exception("Table '%s' of type '%s' does not exist when expected" % (tablename, table_type))

@then('verify that there is a "{table_type}" table "{tablename}" in "{dbname}" with "{numrows}" rows')
def impl(context, table_type, tablename, dbname, numrows):
    if not check_table_exists(context, dbname=dbname, table_name=tablename, table_type=table_type):
        raise Exception("Table '%s' of type '%s' does not exist when expected" % (tablename, table_type))
        with dbconn.connect(dbconn.DbURL(dbname=dbname)) as conn:
            rowcount = dbconn.execSQLForSingleton(conn, "SELECT count(*) FROM %s" % tablename)
            if rowcount != numrows:
                raise Exception("Expected to find %d rows in table %s, found %d" % (numrows, tablename, rowcount))

@then(
    'data for partition table "{table_name}" with partition level "{part_level}" is distributed across all segments on "{dbname}"')
def impl(context, table_name, part_level, dbname):
    validate_part_table_data_on_segments(context, table_name, part_level, dbname)

@then('verify that table "{tname}" in "{dbname}" has "{nrows}" rows')
def impl(context, tname, dbname, nrows):
    check_row_count(context, tname, dbname, int(nrows))

@then(
    'verify that table "{src_tname}" in database "{src_dbname}" of source system has same data with table "{dest_tname}" in database "{dest_dbname}" of destination system with options "{options}"')
def impl(context, src_tname, src_dbname, dest_tname, dest_dbname, options):
    match_table_select(context, src_tname, src_dbname, dest_tname, dest_dbname, options)


@then(
    'verify that table "{src_tname}" in database "{src_dbname}" of source system has same data with table "{dest_tname}" in database "{dest_dbname}" of destination system with order by "{orderby}"')
def impl(context, src_tname, src_dbname, dest_tname, dest_dbname, orderby):
    match_table_select(context, src_tname, src_dbname, dest_tname, dest_dbname, orderby)

@given('schema "{schema_list}" exists in "{dbname}"')
@then('schema "{schema_list}" exists in "{dbname}"')
def impl(context, schema_list, dbname):
    schemas = [s.strip() for s in schema_list.split(',')]
    for s in schemas:
        drop_schema_if_exists(context, s.strip(), dbname)
        create_schema(context, s.strip(), dbname)


@then('the temporary file "{filename}" is removed')
def impl(context, filename):
    if os.path.exists(filename):
        os.remove(filename)


@then('the temporary table file "{filename}" is removed')
def impl(context, filename):
    table_file = 'test/behave/mgmt_utils/steps/data/gptransfer/%s' % filename
    if os.path.exists(table_file):
        os.remove(table_file)


def create_table_file_locally(context, filename, table_list, location=os.getcwd()):
    tables = table_list.split('|')
    file_path = os.path.join(location, filename)
    with open(file_path, 'w') as fp:
        for t in tables:
            fp.write(t + '\n')
    context.filename = file_path


@given('there is a file "{filename}" with tables "{table_list}"')
@then('there is a file "{filename}" with tables "{table_list}"')
def impl(context, filename, table_list):
    create_table_file_locally(context, filename, table_list)


@given('the row "{row_values}" is inserted into "{table}" in "{dbname}"')
def impl(context, row_values, table, dbname):
    insert_row(context, row_values, table, dbname)


@then('verify that database "{dbname}" does not exist')
def impl(context, dbname):
    with dbconn.connect(dbconn.DbURL(dbname='template1')) as conn:
        sql = """SELECT datname FROM pg_database"""
        dbs = dbconn.execSQL(conn, sql)
        if dbname in dbs:
            raise Exception('Database exists when it shouldnt "%s"' % dbname)


@given('the file "{filepath}" exists under master data directory')
def impl(context, filepath):
    fullfilepath = os.path.join(master_data_dir, filepath)
    if not os.path.isdir(os.path.dirname(fullfilepath)):
        os.makedirs(os.path.dirname(fullfilepath))
    open(fullfilepath, 'a').close()

@then('the file "{filepath}" does not exist under standby master data directory')
def impl(context, filepath):
    fullfilepath = os.path.join(context.standby_data_dir, filepath)
    cmd = "ls -al %s" % fullfilepath
    try:
        run_command_remote(context,
                           cmd,
                           context.standby_hostname,
                           os.getenv("GPHOME") + '/greenplum_path.sh',
                           'export MASTER_DATA_DIRECTORY=%s' % context.standby_data_dir,
                           validateAfter=True)
    except:
        pass
    else:
        raise Exception("file '%s' should not exist in standby master data directory" % fullfilepath)

@given('results of the sql "{sql}" db "{dbname}" are stored in the context')
@when( 'results of the sql "{sql}" db "{dbname}" are stored in the context')
def impl(context, sql, dbname):
    context.stored_sql_results = []

    with dbconn.connect(dbconn.DbURL(dbname=dbname)) as conn:
        curs = dbconn.execSQL(conn, sql)
        context.stored_sql_results = curs.fetchall()


@then('validate that following rows are in the stored rows')
def impl(context):
    for row in context.table:
        found_match = False

        for stored_row in context.stored_rows:
            match_this_row = True

            for i in range(len(stored_row)):
                value = row[i]

                if isinstance(stored_row[i], bool):
                    value = str(True if row[i] == 't' else False)

                if value != str(stored_row[i]):
                    match_this_row = False
                    break

            if match_this_row:
                found_match = True
                break

        if not found_match:
            print context.stored_rows
            raise Exception("'%s' not found in stored rows" % row)


@then('validate that first column of first stored row has "{numlines}" lines of raw output')
def impl(context, numlines):
    raw_lines_count = len(context.stored_rows[0][0].splitlines())
    numlines = int(numlines)
    if raw_lines_count != numlines:
        raise Exception("Found %d of stored query result but expected %d records" % (raw_lines_count, numlines))


def get_standby_host():
    gparray = GpArray.initFromCatalog(dbconn.DbURL())
    segments = gparray.getDbList()
    standby_master = [seg.getSegmentHostName() for seg in segments if seg.isSegmentStandby()]
    if len(standby_master) > 0:
        return standby_master[0]
    else:
        return []


@given('user does not have ssh permissions')
def impl(context):
    user_home = os.environ.get('HOME')
    authorized_keys_file = '%s/.ssh/authorized_keys' % user_home
    if os.path.exists(os.path.abspath(authorized_keys_file)):
        shutil.move(authorized_keys_file, '%s.bk' % authorized_keys_file)


@then('user has ssh permissions')
def impl(context):
    user_home = os.environ.get('HOME')
    authorized_keys_backup_file = '%s/.ssh/authorized_keys.bk' % user_home
    if os.path.exists(authorized_keys_backup_file):
        shutil.move(authorized_keys_backup_file, authorized_keys_backup_file[:-3])

def run_gpinitstandby(context, hostname, port, standby_data_dir, options='', remote=False):
    if '-n' in options:
        cmd = "gpinitstandby -a"
    elif remote:
        #if standby_data_dir exists on $hostname, remove it
        remove_dir(hostname, standby_data_dir)
        # create the data dir on $hostname
        create_dir(hostname, os.path.dirname(standby_data_dir))
        # We do not set port nor data dir here to test gpinitstandby's ability to autogather that info
        cmd = "gpinitstandby -a -s %s" % hostname
    else:
        cmd = "gpinitstandby -a -s %s -P %s -F %s" % (hostname, port, standby_data_dir)

    run_gpcommand(context, cmd + ' ' + options)

@when('the user initializes a standby on the same host as master with same port')
def impl(context):
    hostname = get_master_hostname('postgres')[0][0]
    temp_data_dir = tempfile.mkdtemp() + "/standby_datadir"
    run_gpinitstandby(context, hostname, os.environ.get("PGPORT"), temp_data_dir)

@when('the user runs gpinitstandby with options "{options}"')
@then('the user runs gpinitstandby with options "{options}"')
@given('the user runs gpinitstandby with options "{options}"')
def impl(context, options):
    dbname = 'postgres'
    with dbconn.connect(dbconn.DbURL(port=os.environ.get("PGPORT"), dbname=dbname)) as conn:
        query = """select distinct content, hostname from gp_segment_configuration order by content limit 2;"""
        cursor = dbconn.execSQL(conn, query)

    try:
        _, master_hostname = cursor.fetchone()
        _, segment_hostname = cursor.fetchone()
    except:
        raise Exception("Did not get two rows from query: %s" % query)

    # if we have two hosts, assume we're testing on a multinode cluster
    if master_hostname != segment_hostname:
        context.standby_hostname = segment_hostname
        context.standby_port = os.environ.get("PGPORT")
        remote = True
    else:
        context.standby_hostname = master_hostname
        context.standby_port = get_open_port()
        remote = False

    # -n option assumes gpinitstandby already ran and put standby in catalog
    if "-n" not in options:
        if remote:
            context.standby_data_dir = master_data_dir
        else:
            context.standby_data_dir = tempfile.mkdtemp() + "/standby_datadir"

    run_gpinitstandby(context, context.standby_hostname, context.standby_port, context.standby_data_dir, options, remote)
    context.master_hostname = master_hostname
    context.master_port = os.environ.get("PGPORT")
    context.standby_was_initialized = True

@when('the user runs gpactivatestandby with options "{options}"')
@then('the user runs gpactivatestandby with options "{options}"')
def impl(context, options):
    context.execute_steps(u'''Then the user runs command "gpactivatestandby -a %s" from standby master''' % options)
    context.standby_was_activated = True

@then('the user runs command "{command}" from standby master')
def impl(context, command):
    cmd = "PGPORT=%s %s" % (context.standby_port, command)
    run_command_remote(context,
                       cmd,
                       context.standby_hostname,
                       os.getenv("GPHOME") + '/greenplum_path.sh',
                       'export MASTER_DATA_DIRECTORY=%s' % context.standby_data_dir,
                       validateAfter=False)

@when('the master goes down')
@then('the master goes down')
def impl(context):
	master = MasterStop("Stopping Master", master_data_dir, mode='immediate')
	master.run()

@when('the standby master goes down')
def impl(context):
	master = MasterStop("Stopping Master Standby", context.standby_data_dir, mode='immediate', ctxt=REMOTE,
                        remoteHost=context.standby_hostname)
	master.run(validateAfter=True)

@then('clean up and revert back to original master')
def impl(context):
    # TODO: think about preserving the master data directory for debugging
    shutil.rmtree(master_data_dir, ignore_errors=True)

    if context.master_hostname != context.standby_hostname:
        # We do not set port nor data dir here to test gpinitstandby's ability to autogather that info
        cmd = "gpinitstandby -a -s %s" % context.master_hostname
    else:
        cmd = "gpinitstandby -a -s %s -P %s -F %s" % (context.master_hostname, context.master_port, master_data_dir)

    context.execute_steps(u'''Then the user runs command "%s" from standby master''' % cmd)

    master = MasterStop("Stopping current master", context.standby_data_dir, mode='immediate', ctxt=REMOTE,
                        remoteHost=context.standby_hostname)
    master.run()

    cmd = "gpactivatestandby -a -d %s" % master_data_dir
    run_gpcommand(context, cmd)

# from https://stackoverflow.com/questions/2838244/get-open-tcp-port-in-python/2838309#2838309
def get_open_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("",0))
    s.listen(1)
    port = s.getsockname()[1]
    s.close()
    return port


@given('"{path}" has its permissions set to "{perm}"')
def impl(context, path, perm):
    path = os.path.expandvars(path)
    if not os.path.exists(path):
        raise Exception('Path does not exist! "%s"' % path)
    old_permissions = os.stat(path).st_mode  # keep it as a number that has a meaningful representation in octal
    test_permissions = int(perm, 8)          # accept string input with octal semantics and convert to a raw number
    os.chmod(path, test_permissions)
    context.path_for_which_to_restore_the_permissions = path
    context.permissions_to_restore_path_to = old_permissions


@then('rely on environment.py to restore path permissions')
def impl(context):
    print "go look in environment.py to see how it uses the path and permissions on context to make sure it's cleaned up"


@when('the user runs pg_controldata against the standby data directory')
def impl(context):
    cmd = "pg_controldata " + context.standby_data_dir
    run_command_remote(context,
                       cmd,
                       context.standby_hostname,
                       os.getenv("GPHOME") + '/greenplum_path.sh',
                       'export MASTER_DATA_DIRECTORY=%s' % context.standby_data_dir)

@given('we have exchanged keys with the cluster')
def impl(context):
    hostlist = get_all_hostnames_as_list(context, 'template1')
    host_str = ' -h '.join(hostlist)
    cmd_str = 'gpssh-exkeys %s' % host_str
    run_gpcommand(context, cmd_str)


@given('user kills a primary postmaster process')
@when('user kills a primary postmaster process')
@then('user kills a primary postmaster process')
def impl(context):
    if hasattr(context, 'pseg'):
        seg_data_dir = context.pseg_data_dir
        seg_host = context.pseg_hostname
        seg_port = context.pseg.getSegmentPort()
    else:
        gparray = GpArray.initFromCatalog(dbconn.DbURL())
        for seg in gparray.getDbList():
            if seg.isSegmentPrimary():
                seg_data_dir = seg.getSegmentDataDirectory()
                seg_host = seg.getSegmentHostName()
                seg_port = seg.getSegmentPort()
                break

    pid = get_pid_for_segment(seg_data_dir, seg_host)
    if pid is None:
        raise Exception('Unable to locate segment "%s" on host "%s"' % (seg_data_dir, seg_host))

    kill_process(int(pid), seg_host, signal.SIGKILL)

    has_process_eventually_stopped(pid, seg_host)

    pid = get_pid_for_segment(seg_data_dir, seg_host)
    if pid is not None:
        raise Exception('Unable to kill postmaster with pid "%d" datadir "%s"' % (pid, seg_data_dir))

    context.killed_seg_host = seg_host
    context.killed_seg_port = seg_port


@given('user kills all primary processes')
@when('user kills all primary processes')
@then('user kills all primary processes')
def impl(context):
    gparray = GpArray.initFromCatalog(dbconn.DbURL())
    for seg in gparray.getDbList():
        if seg.isSegmentPrimary():
            seg_data_dir = seg.getSegmentDataDirectory()
            seg_host = seg.getSegmentHostName()
            seg_port = seg.getSegmentPort()

            pid = get_pid_for_segment(seg_data_dir, seg_host)
            if pid is None:
                raise Exception('Unable to locate segment "%s" on host "%s"' % (seg_data_dir, seg_host))

            kill_process(int(pid), seg_host, signal.SIGKILL)

            has_process_eventually_stopped(pid, seg_host)

            pid = get_pid_for_segment(seg_data_dir, seg_host)
            if pid is not None:
                raise Exception('Unable to kill postmaster with pid "%d" datadir "%s"' % (pid, seg_data_dir))


@given('user can start transactions')
@when('user can start transactions')
@then('user can start transactions')
def impl(context):
    num_retries = 150
    attempt = 0
    while attempt < num_retries:
        try:
            with dbconn.connect(dbconn.DbURL()) as conn:
                break
        except Exception as e:
            attempt += 1
            pass
        time.sleep(1)

    if attempt == num_retries:
        raise Exception('Unable to establish a connection to database !!!')


@given('the environment variable "{var}" is not set')
def impl(context, var):
    context.env_var = os.environ.get(var)
    os.environ[var] = ''


@given('the environment variable "{var}" is set to "{val}"')
def impl(context, var, val):
    context.env_var = os.environ.get(var)
    os.environ[var] = val


@given('below sql is executed in "{dbname}" db')
@when('below sql is executed in "{dbname}" db')
def impl(context, dbname):
    sql = context.text
    execute_sql(dbname, sql)


@when('sql "{sql}" is executed in "{dbname}" db')
@then('sql "{sql}" is executed in "{dbname}" db')
def impl(context, sql, dbname):
    execute_sql(dbname, sql)


@when('execute following sql in db "{dbname}" and store result in the context')
def impl(context, dbname):
    context.stored_rows = []

    with dbconn.connect(dbconn.DbURL(dbname=dbname)) as conn:
        curs = dbconn.execSQL(conn, context.text)
        context.stored_rows = curs.fetchall()


@when('execute sql "{sql}" in db "{dbname}" and store result in the context')
def impl(context, sql, dbname):
    context.stored_rows = []

    with dbconn.connect(dbconn.DbURL(dbname=dbname)) as conn:
        curs = dbconn.execSQL(conn, sql)
        context.stored_rows = curs.fetchall()


@then('validate that "{message}" is in the stored rows')
def impl(context, message):
    for row in context.stored_rows:
        for column in row:
            if message in column:
                return

    print context.stored_rows
    print message
    raise Exception("'%s' not found in stored rows" % message)


@then('verify that file "{filename}" exists under "{path}"')
def impl(context, filename, path):
    fullpath = "%s/%s" % (path, filename)
    fullpath = os.path.expandvars(fullpath)

    if not os.path.exists(fullpath):
        raise Exception('file "%s" is not exist' % fullpath)


@given('waiting "{second}" seconds')
@when('waiting "{second}" seconds')
@then('waiting "{second}" seconds')
def impl(context, second):
    time.sleep(float(second))


def get_opened_files(filename, pidfile):
    cmd = "if [ `uname -s` = 'SunOS' ]; then CMD=pfiles; else CMD='lsof -p'; fi && PATH=$PATH:/usr/bin:/usr/sbin $CMD `cat %s` | grep %s | wc -l" % (
    pidfile, filename)
    return commands.getstatusoutput(cmd)


@when('table "{tablename}" is dropped in "{dbname}"')
@then('table "{tablename}" is dropped in "{dbname}"')
@given('table "{tablename}" is dropped in "{dbname}"')
def impl(context, tablename, dbname):
    drop_table_if_exists(context, table_name=tablename, dbname=dbname)


@given('all the segments are running')
@when('all the segments are running')
@then('all the segments are running')
def impl(context):
    if not are_segments_running():
        raise Exception("all segments are not currently running")

    return


@given('the "{seg}" segment information is saved')
@when('the "{seg}" segment information is saved')
@then('the "{seg}" segment information is saved')
def impl(context, seg):
    gparray = GpArray.initFromCatalog(dbconn.DbURL())

    if seg == "primary":
        primary_segs = [seg for seg in gparray.getDbList() if seg.isSegmentPrimary()]
        context.pseg = primary_segs[0]
        context.pseg_data_dir = context.pseg.getSegmentDataDirectory()
        context.pseg_hostname = context.pseg.getSegmentHostName()
        context.pseg_dbid = context.pseg.getSegmentDbId()
    elif seg == "mirror":
        mirror_segs = [seg for seg in gparray.getDbList() if seg.isSegmentMirror()]
        context.mseg = mirror_segs[0]
        context.mseg_hostname = context.mseg.getSegmentHostName()
        context.mseg_dbid = context.mseg.getSegmentDbId()
        context.mseg_data_dir = context.mseg.getSegmentDataDirectory()


@when('we run a sample background script to generate a pid on "{seg}" segment')
def impl(context, seg):
    if seg == "primary":
        if not hasattr(context, 'pseg_hostname'):
            raise Exception("primary seg host is not saved in the context")
        hostname = context.pseg_hostname
    elif seg == "smdw":
        if not hasattr(context, 'standby_host'):
            raise Exception("Standby host is not saved in the context")
        hostname = context.standby_host

    filename = os.path.join(os.getcwd(), './test/behave/mgmt_utils/steps/data/pid_background_script.py')

    cmd = Command(name="Remove background script on remote host", cmdStr='rm -f /tmp/pid_background_script.py',
                  remoteHost=hostname, ctxt=REMOTE)
    cmd.run(validateAfter=True)

    cmd = Command(name="Copy background script to remote host", cmdStr='scp %s %s:/tmp' % (filename, hostname))
    cmd.run(validateAfter=True)

    cmd = Command(name="Run Bg process to save pid",
                  cmdStr='sh -c "python /tmp/pid_background_script.py" &>/dev/null &', remoteHost=hostname, ctxt=REMOTE)
    cmd.run(validateAfter=True)

    cmd = Command(name="get bg pid", cmdStr="ps ux | grep pid_background_script.py | grep -v grep | awk '{print \$2}'",
                  remoteHost=hostname, ctxt=REMOTE)
    cmd.run(validateAfter=True)
    context.bg_pid = cmd.get_stdout()
    if not context.bg_pid:
        raise Exception("Unable to obtain the pid of the background script. Seg Host: %s, get_results: %s" %
                        (hostname, cmd.get_stdout()))


@when('the background pid is killed on "{seg}" segment')
@then('the background pid is killed on "{seg}" segment')
def impl(context, seg):
    if seg == "primary":
        if not hasattr(context, 'pseg_hostname'):
            raise Exception("primary seg host is not saved in the context")
        hostname = context.pseg_hostname
    elif seg == "smdw":
        if not hasattr(context, 'standby_host'):
            raise Exception("Standby host is not saved in the context")
        hostname = context.standby_host

    cmd = Command(name="get bg pid", cmdStr="ps ux | grep pid_background_script.py | grep -v grep | awk '{print \$2}'",
                  remoteHost=hostname, ctxt=REMOTE)
    cmd.run(validateAfter=True)
    pids = cmd.get_stdout().splitlines()
    for pid in pids:
        cmd = Command(name="killbg pid", cmdStr='kill -9 %s' % pid, remoteHost=hostname, ctxt=REMOTE)
        cmd.run(validateAfter=True)


@when('we generate the postmaster.pid file with the background pid on "{seg}" segment')
def impl(context, seg):
    if seg == "primary":
        if not hasattr(context, 'pseg_hostname'):
            raise Exception("primary seg host is not saved in the context")
        hostname = context.pseg_hostname
        data_dir = context.pseg_data_dir
    elif seg == "smdw":
        if not hasattr(context, 'standby_host'):
            raise Exception("Standby host is not saved in the context")
        hostname = context.standby_host
        data_dir = context.standby_host_data_dir

    pid_file = os.path.join(data_dir, 'postmaster.pid')
    pid_file_orig = pid_file + '.orig'

    cmd = Command(name="Copy pid file", cmdStr='cp %s %s' % (pid_file_orig, pid_file), remoteHost=hostname, ctxt=REMOTE)
    cmd.run(validateAfter=True)

    cpCmd = Command(name='copy pid file to master for editing', cmdStr='scp %s:%s /tmp' % (hostname, pid_file))

    cpCmd.run(validateAfter=True)

    with open('/tmp/postmaster.pid', 'r') as fr:
        lines = fr.readlines()

    lines[0] = "%s\n" % context.bg_pid

    with open('/tmp/postmaster.pid', 'w') as fw:
        fw.writelines(lines)

    cpCmd = Command(name='copy pid file to segment after editing',
                    cmdStr='scp /tmp/postmaster.pid %s:%s' % (hostname, pid_file))
    cpCmd.run(validateAfter=True)


@when('we generate the postmaster.pid file with a non running pid on the same "{seg}" segment')
def impl(context, seg):
    if seg == "primary":
        data_dir = context.pseg_data_dir
        hostname = context.pseg_hostname
    elif seg == "mirror":
        data_dir = context.mseg_data_dir
        hostname = context.mseg_hostname
    elif seg == "smdw":
        if not hasattr(context, 'standby_host'):
            raise Exception("Standby host is not saved in the context")
        hostname = context.standby_host
        data_dir = context.standby_host_data_dir

    pid_file = os.path.join(data_dir, 'postmaster.pid')
    pid_file_orig = pid_file + '.orig'

    cmd = Command(name="Copy pid file", cmdStr='cp %s %s' % (pid_file_orig, pid_file), remoteHost=hostname, ctxt=REMOTE)
    cmd.run(validateAfter=True)

    cpCmd = Command(name='copy pid file to master for editing', cmdStr='scp %s:%s /tmp' % (hostname, pid_file))

    cpCmd.run(validateAfter=True)

    # Since Command creates a short-lived SSH session, we observe the PID given
    # a throw-away remote process. Assume that the PID is unused and available on
    # the remote in the near future.
    # This pid is no longer associated with a
    # running process and won't be recycled for long enough that tests
    # have finished.
    cmd = Command(name="get non-existing pid", cmdStr="echo \$\$", remoteHost=hostname, ctxt=REMOTE)
    cmd.run(validateAfter=True)
    pid = cmd.get_results().stdout.strip()

    with open('/tmp/postmaster.pid', 'r') as fr:
        lines = fr.readlines()

    lines[0] = "%s\n" % pid

    with open('/tmp/postmaster.pid', 'w') as fw:
        fw.writelines(lines)

    cpCmd = Command(name='copy pid file to segment after editing',
                    cmdStr='scp /tmp/postmaster.pid %s:%s' % (hostname, pid_file))
    cpCmd.run(validateAfter=True)


@when('the user starts one "{seg}" segment')
def impl(context, seg):
    if seg == "primary":
        dbid = context.pseg_dbid
        hostname = context.pseg_hostname
        segment = context.pseg
    elif seg == "mirror":
        dbid = context.mseg_dbid
        hostname = context.mseg_hostname
        segment = context.mseg

    segStartCmd = SegmentStart(name="Starting new segment dbid %s on host %s." % (str(dbid), hostname)
                               , gpdb=segment
                               , numContentsInCluster=0  # Starting seg on it's own.
                               , era=None
                               , mirrormode=MIRROR_MODE_MIRRORLESS
                               , utilityMode=False
                               , ctxt=REMOTE
                               , remoteHost=hostname
                               , pg_ctl_wait=True
                               , timeout=300)
    segStartCmd.run(validateAfter=True)


@when('the postmaster.pid file on "{seg}" segment is saved')
def impl(context, seg):
    if seg == "primary":
        data_dir = context.pseg_data_dir
        hostname = context.pseg_hostname
    elif seg == "mirror":
        data_dir = context.mseg_data_dir
        hostname = context.mseg_hostname
    elif seg == "smdw":
        if not hasattr(context, 'standby_host'):
            raise Exception("Standby host is not saved in the context")
        hostname = context.standby_host
        data_dir = context.standby_host_data_dir

    pid_file = os.path.join(data_dir, 'postmaster.pid')
    pid_file_orig = pid_file + '.orig'

    cmd = Command(name="Copy pid file", cmdStr='cp %s %s' % (pid_file, pid_file_orig), remoteHost=hostname, ctxt=REMOTE)
    cmd.run(validateAfter=True)


@then('the backup pid file is deleted on "{seg}" segment')
def impl(context, seg):
    if seg == "primary":
        data_dir = context.pseg_data_dir
        hostname = context.pseg_hostname
    elif seg == "mirror":
        data_dir = context.mseg_data_dir
        hostname = context.mseg_hostname
    elif seg == "smdw":
        data_dir = context.standby_host_data_dir
        hostname = context.standby_host

    cmd = Command(name="Remove pid file", cmdStr='rm -f %s' % (os.path.join(data_dir, 'postmaster.pid.orig')),
                  remoteHost=hostname, ctxt=REMOTE)
    cmd.run(validateAfter=True)


@given('the standby is not initialized')
@then('the standby is not initialized')
def impl(context):
    standby = get_standby_host()
    if standby:
        context.cluster_had_standby = True
        context.standby_host = standby
        run_gpcommand(context, 'gpinitstandby -ra')

@then('verify the standby master entries in catalog')
def impl(context):
	check_segment_config_query = "SELECT * FROM gp_segment_configuration WHERE content = -1 AND role = 'm'"
	check_stat_replication_query = "SELECT * FROM pg_stat_replication"
	with dbconn.connect(dbconn.DbURL(dbname='postgres')) as conn:
		segconfig = dbconn.execSQL(conn, check_segment_config_query).fetchall()
		statrep = dbconn.execSQL(conn, check_stat_replication_query).fetchall()

	context.standby_dbid = segconfig[0][0]

	if len(segconfig) != 1:
		raise Exception("gp_segment_configuration did not have standby master")

	if len(statrep) != 1:
		raise Exception("pg_stat_replication did not have standby master")

@then('verify the standby master is now acting as master')
def impl(context):
	check_segment_config_query = "SELECT * FROM gp_segment_configuration WHERE content = -1 AND role = 'p' AND preferred_role = 'p' AND dbid = %s" % context.standby_dbid
	with dbconn.connect(dbconn.DbURL(hostname=context.standby_hostname, dbname='postgres', port=context.standby_port)) as conn:
		segconfig = dbconn.execSQL(conn, check_segment_config_query).fetchall()

	if len(segconfig) != 1:
		raise Exception("gp_segment_configuration did not have standby master acting as new master")

@then('verify that the schema "{schema_name}" exists in "{dbname}"')
def impl(context, schema_name, dbname):
    schema_exists = check_schema_exists(context, schema_name, dbname)
    if not schema_exists:
        raise Exception("Schema '%s' does not exist in the database '%s'" % (schema_name, dbname))


@then('verify that the utility {utilname} ever does logging into the user\'s "{dirname}" directory')
def impl(context, utilname, dirname):
    absdirname = "%s/%s" % (os.path.expanduser("~"), dirname)
    if not os.path.exists(absdirname):
        raise Exception('No such directory: %s' % absdirname)
    pattern = "%s/%s_*.log" % (absdirname, utilname)
    logs_for_a_util = glob.glob(pattern)
    if not logs_for_a_util:
        raise Exception('Logs matching "%s" were not created' % pattern)


@then('verify that a log was created by {utilname} in the "{dirname}" directory')
def impl(context, utilname, dirname):
    if not os.path.exists(dirname):
        raise Exception('No such directory: %s' % dirname)
    pattern = "%s/%s_*.log" % (dirname, utilname)
    logs_for_a_util = glob.glob(pattern)
    if not logs_for_a_util:
        raise Exception('Logs matching "%s" were not created' % pattern)


@given('a table is created containing rows of length "{length}" with connection "{dbconn}"')
def impl(context, length, dbconn):
    length = int(length)
    wide_row_file = 'test/behave/mgmt_utils/steps/data/gptransfer/wide_row_%s.sql' % length
    tablename = 'public.wide_row_%s' % length
    entry = "x" * length
    with open(wide_row_file, 'w') as sql_file:
        sql_file.write("CREATE TABLE %s (a integer, b text);\n" % tablename)
        for i in range(10):
            sql_file.write("INSERT INTO %s VALUES (%d, \'%s\');\n" % (tablename, i, entry))
    command = '%s -f %s' % (dbconn, wide_row_file)
    run_gpcommand(context, command)


@then('drop the table "{tablename}" with connection "{dbconn}"')
def impl(context, tablename, dbconn):
    command = "%s -c \'drop table if exists %s\'" % (dbconn, tablename)
    run_gpcommand(context, command)




def _get_gpAdminLogs_directory():
    return "%s/gpAdminLogs" % os.path.expanduser("~")


@given('an incomplete map file is created')
def impl(context):
    with open('/tmp/incomplete_map_file', 'w') as fd:
        fd.write('nonexistent_host,nonexistent_host')


@given(
    'there is a table "{table_name}" dependent on function "{func_name}" in database "{dbname}" on the source system')
def impl(context, table_name, func_name, dbname):
    dbconn = 'psql -d %s -p $GPTRANSFER_SOURCE_PORT -U $GPTRANSFER_SOURCE_USER -h $GPTRANSFER_SOURCE_HOST' % dbname
    SQL = """CREATE TABLE %s (num integer); CREATE FUNCTION %s (integer) RETURNS integer AS 'select abs(\$1);' LANGUAGE SQL IMMUTABLE; CREATE INDEX test_index ON %s (%s(num))""" % (
    table_name, func_name, table_name, func_name)
    command = '%s -c "%s"' % (dbconn, SQL)
    run_command(context, command)


@then('verify that function "{func_name}" exists in database "{dbname}"')
def impl(context, func_name, dbname):
    SQL = """SELECT proname FROM pg_proc WHERE proname = '%s';""" % func_name
    row_count = getRows(dbname, SQL)[0][0]
    if row_count != 'test_function':
        raise Exception('Function %s does not exist in %s"' % (func_name, dbname))

@then('verify that sequence "{seq_name}" last value is "{last_value}" in database "{dbname}"')
@when('verify that sequence "{seq_name}" last value is "{last_value}" in database "{dbname}"')
@given('verify that sequence "{seq_name}" last value is "{last_value}" in database "{dbname}"')
def impl(context, seq_name, last_value, dbname):
    SQL = """SELECT last_value FROM %s;""" % seq_name
    lv = getRows(dbname, SQL)[0][0]
    if lv != int(last_value):
        raise Exception('Sequence %s last value is not %s in %s"' % (seq_name, last_value, dbname))

@given('the user runs the command "{cmd}" in the background')
@when('the user runs the command "{cmd}" in the background')
def impl(context, cmd):
    thread.start_new_thread(run_command, (context, cmd))
    time.sleep(10)


@given('the user runs the command "{cmd}" in the background without sleep')
@when('the user runs the command "{cmd}" in the background without sleep')
def impl(context, cmd):
    thread.start_new_thread(run_command, (context, cmd))


@then('verify that the file "{filename}" contains the string "{output}"')
def impl(context, filename, output):
    contents = ''
    with open(filename) as fr:
        for line in fr:
            contents = line.strip()
    print contents
    check_stdout_msg(context, output)


@then('verify that the last line of the file "{filename}" in the master data directory contains the string "{output}"')
def impl(context, filename, output):
    contents = ''
    file_path = os.path.join(master_data_dir, filename)
    with open(file_path) as fr:
        for line in fr:
            contents = line.strip()
    pat = re.compile(output)
    if not pat.search(contents):
        err_str = "Expected stdout string '%s' and found: '%s'" % (output, contents)
        raise Exception(err_str)


@then('the user waits for "{process_name}" to finish running')
def impl(context, process_name):
    run_command(context, "ps ux | grep `which %s` | grep -v grep | awk '{print $2}' | xargs" % process_name)
    pids = context.stdout_message.split()
    while len(pids) > 0:
        for pid in pids:
            try:
                os.kill(int(pid), 0)
            except OSError:
                pids.remove(pid)
        time.sleep(10)


@given('the gpfdists occupying port {port} on host "{hostfile}"')
def impl(context, port, hostfile):
    remote_gphome = os.environ.get('GPHOME')
    gp_source_file = os.path.join(remote_gphome, 'greenplum_path.sh')
    source_map_file = os.environ.get(hostfile)
    dir = '/tmp'
    ctxt = 2
    with open(source_map_file, 'r') as f:
        for line in f:
            host = line.strip().split(',')[0]
            if host in ('localhost', '127.0.0.1', socket.gethostname()):
                ctxt = 1
            gpfdist = Gpfdist('gpfdist on host %s' % host, dir, port, os.path.join('/tmp', 'gpfdist.pid'),
                              ctxt, host, gp_source_file)
            gpfdist.startGpfdist()


@then('the gpfdists running on port {port} get cleaned up from host "{hostfile}"')
def impl(context, port, hostfile):
    remote_gphome = os.environ.get('GPHOME')
    gp_source_file = os.path.join(remote_gphome, 'greenplum_path.sh')
    source_map_file = os.environ.get(hostfile)
    dir = '/tmp'
    ctxt = 2
    with open(source_map_file, 'r') as f:
        for line in f:
            host = line.strip().split(',')[0]
            if host in ('localhost', '127.0.0.1', socket.gethostname()):
                ctxt = 1
            gpfdist = Gpfdist('gpfdist on host %s' % host, dir, port, os.path.join('/tmp', 'gpfdist.pid'),
                              ctxt, host, gp_source_file)
            gpfdist.cleanupGpfdist()


@then('verify that the query "{query}" in database "{dbname}" returns "{nrows}"')
def impl(context, dbname, query, nrows):
    check_count_for_specific_query(dbname, query, int(nrows))


@then('verify that the file "{filepath}" contains "{line}"')
def impl(context, filepath, line):
    filepath = glob.glob(filepath)[0]
    if line not in open(filepath).read():
        raise Exception("The file '%s' does not contain '%s'" % (filepath, line))


@then('verify that the file "{filepath}" does not contain "{line}"')
def impl(context, filepath, line):
    filepath = glob.glob(filepath)[0]
    if line in open(filepath).read():
        raise Exception("The file '%s' does contain '%s'" % (filepath, line))


@then('verify that gptransfer is in order of "{filepath}" when partition transfer is "{is_partition_transfer}"')
def impl(context, filepath, is_partition_transfer):
    with open(filepath) as f:
        table = f.read().splitlines()
        if is_partition_transfer != "None":
            table = [x.split(',')[0] for x in table]

    split_message = re.findall("Starting transfer of.*\n", context.stdout_message)

    if len(split_message) == 0 and len(table) != 0:
        raise Exception("There were no tables transfered")

    counter_table = 0
    counter_split = 0
    found = 0

    while counter_table < len(table) and counter_split < len(split_message):
        for i in range(counter_split, len(split_message)):
            pat = table[counter_table] + " to"
            prog = re.compile(pat)
            res = prog.search(split_message[i])
            if not res:
                counter_table += 1
                break
            else:
                found += 1
                counter_split += 1

    if found != len(split_message):
        raise Exception("expected to find %s tables in order and only found %s in order" % (len(split_message), found))


@given('database "{dbname}" is dropped and recreated')
@when('database "{dbname}" is dropped and recreated')
@then('database "{dbname}" is dropped and recreated')
def impl(context, dbname):
    drop_database_if_exists(context, dbname)
    create_database(context, dbname)


@then('validate and run gpcheckcat repair')
def impl(context):
    context.execute_steps(u'''
        Then gpcheckcat should print "repair script\(s\) generated in dir gpcheckcat.repair.*" to stdout
        Then the path "gpcheckcat.repair.*" is found in cwd "1" times
        Then run all the repair scripts in the dir "gpcheckcat.repair.*"
        And the path "gpcheckcat.repair.*" is removed from current working directory
    ''')

@given('there is a "{tabletype}" table "{tablename}" in "{dbname}" with "{numrows}" rows')
def impl(context, tabletype, tablename, dbname, numrows):
    populate_regular_table_data(context, tabletype, tablename, 'None', dbname, with_data=True, rowcount=int(numrows))


@given('there is a "{tabletype}" table "{tablename}" in "{dbname}" with data')
@then('there is a "{tabletype}" table "{tablename}" in "{dbname}" with data')
@when('there is a "{tabletype}" table "{tablename}" in "{dbname}" with data')
def impl(context, tabletype, tablename, dbname):
    populate_regular_table_data(context, tabletype, tablename, 'None', dbname, with_data=True)


@given('there is a "{tabletype}" partition table "{table_name}" in "{dbname}" with data')
@then('there is a "{tabletype}" partition table "{table_name}" in "{dbname}" with data')
@when('there is a "{tabletype}" partition table "{table_name}" in "{dbname}" with data')
def impl(context, tabletype, table_name, dbname):
    create_partition(context, tablename=table_name, storage_type=tabletype, dbname=dbname, with_data=True)


@then('read pid from file "{filename}" and kill the process')
@when('read pid from file "{filename}" and kill the process')
@given('read pid from file "{filename}" and kill the process')
def impl(context, filename):
    retry = 0
    pid = None

    while retry < 5:
        try:
            with open(filename) as fr:
                pid = fr.readline().strip()
            if pid:
                break
        except:
            retry += 1
            time.sleep(retry * 0.1) # 100 millis, 200 millis, etc.

    if not pid:
        raise Exception("process id '%s' not found in the file '%s'" % (pid, filename))

    cmd = Command(name="killing pid", cmdStr='kill -9 %s' % pid)
    cmd.run(validateAfter=True)


@then('an attribute of table "{table}" in database "{dbname}" is deleted on segment with content id "{segid}"')
def impl(context, table, dbname, segid):
    local_cmd = 'psql %s -t -c "SELECT port,hostname FROM gp_segment_configuration WHERE content=%s and role=\'p\';"' % (
    dbname, segid)
    run_command(context, local_cmd)
    port, host = context.stdout_message.split("|")
    port = port.strip()
    host = host.strip()
    user = os.environ.get('USER')
    source_file = os.path.join(os.environ.get('GPHOME'), 'greenplum_path.sh')
    # Yes, the below line is ugly.  It looks much uglier when done with separate strings, given the multiple levels of escaping required.
    remote_cmd = """
ssh %s "source %s; export PGUSER=%s; export PGPORT=%s; export PGOPTIONS=\\\"-c gp_session_role=utility\\\"; psql -d %s -c \\\"SET allow_system_table_mods=true; DELETE FROM pg_attribute where attrelid=\'%s\'::regclass::oid;\\\""
""" % (host, source_file, user, port, dbname, table)
    run_command(context, remote_cmd.strip())


@then('The user runs sql "{query}" in "{dbname}" on first primary segment')
@when('The user runs sql "{query}" in "{dbname}" on first primary segment')
@given('The user runs sql "{query}" in "{dbname}" on first primary segment')
def impl(context, query, dbname):
    host, port = get_primary_segment_host_port()
    psql_cmd = "PGDATABASE=\'%s\' PGOPTIONS=\'-c gp_session_role=utility\' psql -h %s -p %s -c \"%s\"; " % (
    dbname, host, port, query)
    Command(name='Running Remote command: %s' % psql_cmd, cmdStr=psql_cmd).run(validateAfter=True)

@then('The user runs sql "{query}" in "{dbname}" on all the segments')
@when('The user runs sql "{query}" in "{dbname}" on all the segments')
@given('The user runs sql "{query}" in "{dbname}" on all the segments')
def impl(context, query, dbname):
    gparray = GpArray.initFromCatalog(dbconn.DbURL())
    segments = gparray.getDbList()
    for seg in segments:
        host = seg.getSegmentHostName()
        if seg.isSegmentPrimary() or seg.isSegmentMaster():
            port = seg.getSegmentPort()
            psql_cmd = "PGDATABASE=\'%s\' PGOPTIONS=\'-c gp_session_role=utility\' psql -h %s -p %s -c \"%s\"; " % (
            dbname, host, port, query)
            Command(name='Running Remote command: %s' % psql_cmd, cmdStr=psql_cmd).run(validateAfter=True)


@then('The user runs sql file "{file}" in "{dbname}" on all the segments')
@when('The user runs sql file "{file}" in "{dbname}" on all the segments')
@given('The user runs sql file "{file}" in "{dbname}" on all the segments')
def impl(context, file, dbname):
    with open(file) as fd:
        query = fd.read().strip()
    gparray = GpArray.initFromCatalog(dbconn.DbURL())
    segments = gparray.getDbList()
    for seg in segments:
        host = seg.getSegmentHostName()
        if seg.isSegmentPrimary() or seg.isSegmentMaster():
            port = seg.getSegmentPort()
            psql_cmd = "PGDATABASE=\'%s\' PGOPTIONS=\'-c gp_session_role=utility\' psql -h %s -p %s -c \"%s\"; " % (
            dbname, host, port, query)
            Command(name='Running Remote command: %s' % psql_cmd, cmdStr=psql_cmd).run(validateAfter=True)


@then('The path "{path}" is removed from current working directory')
@when('The path "{path}" is removed from current working directory')
@given('The path "{path}" is removed from current working directory')
def impl(context, path):
    remove_local_path(path)


@given('the path "{path}" is found in cwd "{num}" times')
@then('the path "{path}" is found in cwd "{num}" times')
@when('the path "{path}" is found in cwd "{num}" times')
def impl(context, path, num):
    result = validate_local_path(path)
    if result != int(num):
        raise Exception("expected %s items but found %s items in path %s" % (num, result, path))


@then('run all the repair scripts in the dir "{dir}"')
def impl(context, dir):
    command = "cd {0} ; for i in *.sh ; do bash $i; done".format(dir)
    run_command(context, command)


@when(
    'the entry for the table "{user_table}" is removed from "{catalog_table}" with key "{primary_key}" in the database "{db_name}"')
def impl(context, user_table, catalog_table, primary_key, db_name):
    delete_qry = "delete from %s where %s='%s'::regclass::oid;" % (catalog_table, primary_key, user_table)
    with dbconn.connect(dbconn.DbURL(dbname=db_name)) as conn:
        for qry in ["set allow_system_table_mods=true;", "set allow_segment_dml=true;", delete_qry]:
            dbconn.execSQL(conn, qry)
            conn.commit()


@when('the entry for the table "{user_table}" is removed from "{catalog_table}" with key "{primary_key}" in the database "{db_name}" on the first primary segment')
@given('the entry for the table "{user_table}" is removed from "{catalog_table}" with key "{primary_key}" in the database "{db_name}" on the first primary segment')
def impl(context, user_table, catalog_table, primary_key, db_name):
    host, port = get_primary_segment_host_port()
    delete_qry = "delete from %s where %s='%s'::regclass::oid;" % (catalog_table, primary_key, user_table)

    with dbconn.connect(dbconn.DbURL(dbname=db_name, port=port, hostname=host), utility=True,
                        allowSystemTableMods=True) as conn:
        for qry in [delete_qry]:
            dbconn.execSQL(conn, qry)
            conn.commit()


@given('the timestamps in the repair dir are consistent')
@when('the timestamps in the repair dir are consistent')
@then('the timestamps in the repair dir are consistent')
def impl(_):
    repair_regex = "gpcheckcat.repair.*"
    timestamp = ""
    repair_dir = ""
    for file in os.listdir('.'):
        if fnmatch.fnmatch(file, repair_regex):
            repair_dir = file
            timestamp = repair_dir.split('.')[2]

    if not timestamp:
        raise Exception("Timestamp was not found")

    for file in os.listdir(repair_dir):
        if not timestamp in file:
            raise Exception("file found containing inconsistent timestamp")

@when('wait until the process "{proc}" goes down')
@then('wait until the process "{proc}" goes down')
@given('wait until the process "{proc}" goes down')
def impl(context, proc):
    is_stopped = has_process_eventually_stopped(proc)
    context.ret_code = 0 if is_stopped else 1
    if not is_stopped:
        context.error_message = 'The process %s is still running after waiting' % proc
    check_return_code(context, 0)


@when('wait until the process "{proc}" is up')
@then('wait until the process "{proc}" is up')
@given('wait until the process "{proc}" is up')
def impl(context, proc):
    cmd = Command(name='pgrep for %s' % proc, cmdStr="pgrep %s" % proc)
    start_time = current_time = datetime.now()
    while (current_time - start_time).seconds < 120:
        cmd.run()
        if cmd.get_return_code() > 1:
            raise Exception("unexpected problem with gprep, return code: %s" % cmd.get_return_code())
        if cmd.get_return_code() != 1:  # 0 means match
            break
        time.sleep(2)
        current_time = datetime.now()
    context.ret_code = cmd.get_return_code()
    context.error_message = ''
    if context.ret_code > 1:
        context.error_message = 'pgrep internal error'
    check_return_code(context, 0)  # 0 means one or more processes were matched


@when('wait until the results from boolean sql "{sql}" is "{boolean}"')
@then('wait until the results from boolean sql "{sql}" is "{boolean}"')
@given('wait until the results from boolean sql "{sql}" is "{boolean}"')
def impl(context, sql, boolean):
    cmd = Command(name='psql', cmdStr='psql --tuples-only -d gpperfmon -c "%s"' % sql)
    start_time = current_time = datetime.now()
    result = None
    while (current_time - start_time).seconds < 120:
        cmd.run()
        if cmd.get_return_code() != 0:
            break
        result = cmd.get_stdout()
        if _str2bool(result) == _str2bool(boolean):
            break
        time.sleep(2)
        current_time = datetime.now()

    if cmd.get_return_code() != 0:
        context.ret_code = cmd.get_return_code()
        context.error_message = 'psql internal error: %s' % cmd.get_stderr()
        check_return_code(context, 0)
    else:
        if _str2bool(result) != _str2bool(boolean):
            raise Exception("sql output '%s' is not same as '%s'" % (result, boolean))


def _str2bool(string):
    return string.lower().strip() in ['t', 'true', '1', 'yes', 'y']


@given('the user creates an index for table "{table_name}" in database "{db_name}"')
@when('the user creates an index for table "{table_name}" in database "{db_name}"')
@then('the user creates an index for table "{table_name}" in database "{db_name}"')
def impl(context, table_name, db_name):
    index_qry = "create table {0}(i int primary key, j varchar); create index test_index on index_table using bitmap(j)".format(
        table_name)

    with dbconn.connect(dbconn.DbURL(dbname=db_name)) as conn:
        dbconn.execSQL(conn, index_qry)
        conn.commit()


@given('the gptransfer test is initialized')
def impl(context):
    context.execute_steps(u'''
        Given the database is running
        And the database "gptest" does not exist
        And the database "gptransfer_destdb" does not exist
        And the database "gptransfer_testdb1" does not exist
        And the database "gptransfer_testdb3" does not exist
        And the database "gptransfer_testdb4" does not exist
        And the database "gptransfer_testdb5" does not exist
    ''')


@given('gpperfmon is configured and running in qamode')
@then('gpperfmon is configured and running in qamode')
def impl(context):
    target_line = 'qamode = 1'
    gpperfmon_config_file = "%s/gpperfmon/conf/gpperfmon.conf" % os.getenv("MASTER_DATA_DIRECTORY")
    if not check_db_exists("gpperfmon", "localhost"):
        context.execute_steps(u'''
                              When the user runs "gpperfmon_install --port 15432 --enable --password foo"
                              Then gpperfmon_install should return a return code of 0
                              ''')

    if not file_contains_line(gpperfmon_config_file, target_line):
        context.execute_steps(u'''
                              When the user runs command "echo 'qamode = 1' >> $MASTER_DATA_DIRECTORY/gpperfmon/conf/gpperfmon.conf"
                              Then echo should return a return code of 0
                              When the user runs command "echo 'verbose = 1' >> $MASTER_DATA_DIRECTORY/gpperfmon/conf/gpperfmon.conf"
                              Then echo should return a return code of 0
                              When the user runs command "echo 'min_query_time = 0' >> $MASTER_DATA_DIRECTORY/gpperfmon/conf/gpperfmon.conf"
                              Then echo should return a return code of 0
                              When the user runs command "echo 'quantum = 10' >> $MASTER_DATA_DIRECTORY/gpperfmon/conf/gpperfmon.conf"
                              Then echo should return a return code of 0
                              When the user runs command "echo 'harvest_interval = 5' >> $MASTER_DATA_DIRECTORY/gpperfmon/conf/gpperfmon.conf"
                              Then echo should return a return code of 0
                              ''')

    if not is_process_running("gpsmon"):
        context.execute_steps(u'''
                              When the database is not running
                              Then wait until the process "postgres" goes down
                              When the user runs "gpstart -a"
                              Then gpstart should return a return code of 0
                              And verify that a role "gpmon" exists in database "gpperfmon"
                              And verify that the last line of the file "postgresql.conf" in the master data directory contains the string "gpperfmon_log_alert_level=warning"
                              And verify that there is a "heap" table "database_history" in "gpperfmon"
                              Then wait until the process "gpmmon" is up
                              And wait until the process "gpsmon" is up
                              ''')


@given('the setting "{variable_name}" is NOT set in the configuration file "{path_to_file}"')
@when('the setting "{variable_name}" is NOT set in the configuration file "{path_to_file}"')
def impl(context, variable_name, path_to_file):
    path = os.path.join(os.getenv("MASTER_DATA_DIRECTORY"), path_to_file)
    temp_file = "/tmp/gpperfmon_temp_config"
    with open(path) as oldfile, open(temp_file, 'w') as newfile:
        for line in oldfile:
            if variable_name not in line:
                newfile.write(line)
    shutil.move(temp_file, path)


@given('the setting "{setting_string}" is placed in the configuration file "{path_to_file}"')
@when('the setting "{setting_string}" is placed in the configuration file "{path_to_file}"')
def impl(context, setting_string, path_to_file):
    path = os.path.join(os.getenv("MASTER_DATA_DIRECTORY"), path_to_file)
    with open(path, 'a') as f:
        f.write(setting_string)
        f.write("\n")


@given('the latest gpperfmon gpdb-alert log is copied to a file with a fake (earlier) timestamp')
@when('the latest gpperfmon gpdb-alert log is copied to a file with a fake (earlier) timestamp')
def impl(context):
    gpdb_alert_file_path_src = sorted(glob.glob(os.path.join(os.getenv("MASTER_DATA_DIRECTORY"),
                                    "gpperfmon",
                                       "logs",
                                       "gpdb-alert*")))[-1]
    # typical filename would be gpdb-alert-2017-04-26_155335.csv
    # setting the timestamp to a string that starts with `-` (em-dash)
    #   will be sorted (based on ascii) before numeric timestamps
    #   without colliding with a real timestamp
    dest = re.sub(r"_\d{6}\.csv$", "_-takeme.csv", gpdb_alert_file_path_src)

    # Let's wait until there's actually something in the file before actually
    # doing a copy of the log...
    for _ in range(60):
        if os.stat(gpdb_alert_file_path_src).st_size != 0:
            shutil.copy(gpdb_alert_file_path_src, dest)
            context.fake_timestamp_file = dest
            return
        sleep(1)

    raise Exception("File: %s is empty" % gpdb_alert_file_path_src)



@then('the file with the fake timestamp no longer exists')
def impl(context):
    if os.path.exists(context.fake_timestamp_file):
        raise Exception("expected no file at: %s" % context.fake_timestamp_file)

@then('"{gppkg_name}" gppkg files exist on all hosts')
def impl(context, gppkg_name):
    remote_gphome = os.environ.get('GPHOME')
    gparray = GpArray.initFromCatalog(dbconn.DbURL())

    hostlist = get_all_hostnames_as_list(context, 'template1')

    # We can assume the GPDB is installed at the same location for all hosts
    rpm_command_list_all = 'rpm -qa --dbpath %s/share/packages/database' % remote_gphome

    for hostname in set(hostlist):
        cmd = Command(name='check if internal rpm gppkg is installed',
                      cmdStr=rpm_command_list_all,
                      ctxt=REMOTE,
                      remoteHost=hostname)
        cmd.run(validateAfter=True)

        if not gppkg_name in cmd.get_stdout():
            raise Exception( '"%s" gppkg is not installed on host: %s. \nInstalled packages: %s' % (gppkg_name, hostname, cmd.get_stdout()))


@given('"{gppkg_name}" gppkg files do not exist on any hosts')
@when('"{gppkg_name}" gppkg files do not exist on any hosts')
@then('"{gppkg_name}" gppkg files do not exist on any hosts')
def impl(context, gppkg_name):
    remote_gphome = os.environ.get('GPHOME')
    hostlist = get_all_hostnames_as_list(context, 'template1')

    # We can assume the GPDB is installed at the same location for all hosts
    rpm_command_list_all = 'rpm -qa --dbpath %s/share/packages/database' % remote_gphome

    for hostname in set(hostlist):
        cmd = Command(name='check if internal rpm gppkg is installed',
                      cmdStr=rpm_command_list_all,
                      ctxt=REMOTE,
                      remoteHost=hostname)
        cmd.run(validateAfter=True)

        if gppkg_name in cmd.get_stdout():
            raise Exception( '"%s" gppkg is installed on host: %s. \nInstalled packages: %s' % (gppkg_name, hostname, cmd.get_stdout()))


def _remove_gppkg_from_host(context, gppkg_name, is_master_host):
    remote_gphome = os.environ.get('GPHOME')

    if is_master_host:
        hostname = get_master_hostname()[0][0] # returns a list of list
    else:
        hostlist = get_segment_hostlist()
        if not hostlist:
            raise Exception("Current GPDB setup is not a multi-host cluster.")

        # Let's just pick whatever is the first host in the list, it shouldn't
        # matter which one we remove from
        hostname = hostlist[0]

    rpm_command_list_all = 'rpm -qa --dbpath %s/share/packages/database' % remote_gphome
    cmd = Command(name='get all rpm from the host',
                  cmdStr=rpm_command_list_all,
                  ctxt=REMOTE,
                  remoteHost=hostname)
    cmd.run(validateAfter=True)
    installed_gppkgs = cmd.get_stdout_lines()
    if not installed_gppkgs:
        raise Exception("Found no packages installed")

    full_gppkg_name = next((gppkg for gppkg in installed_gppkgs if gppkg_name in gppkg), None)
    if not full_gppkg_name:
        raise Exception("Found no matches for gppkg '%s'\n"
                        "gppkgs installed:\n%s" % (gppkg_name, installed_gppkgs))

    rpm_remove_command = 'rpm -e %s --dbpath %s/share/packages/database' % (full_gppkg_name, remote_gphome)
    cmd = Command(name='Cleanly remove from the remove host',
                  cmdStr=rpm_remove_command,
                  ctxt=REMOTE,
                  remoteHost=hostname)
    cmd.run(validateAfter=True)

    remove_archive_gppgk = 'rm -f %s/share/packages/archive/%s.gppkg' % (remote_gphome, gppkg_name)
    cmd = Command(name='Remove archive gppkg',
                  cmdStr=remove_archive_gppgk,
                  ctxt=REMOTE,
                  remoteHost=hostname)
    cmd.run(validateAfter=True)


@when('gppkg "{gppkg_name}" is removed from a segment host')
def impl(context, gppkg_name):
    _remove_gppkg_from_host(context, gppkg_name, is_master_host=False)


@when('gppkg "{gppkg_name}" is removed from master host')
def impl(context, gppkg_name):
    _remove_gppkg_from_host(context, gppkg_name, is_master_host=True)


@given('gpAdminLogs directory has no "{prefix}" files')
def impl(context, prefix):
    log_dir = _get_gpAdminLogs_directory()
    items = glob.glob('%s/%s_*.log' % (log_dir, prefix))
    for item in items:
        os.remove(item)


@given('"{filepath}" is copied to the install directory')
def impl(context, filepath):
    gphome = os.getenv("GPHOME")
    if not gphome:
        raise Exception("GPHOME must be set")
    shutil.copy(filepath, os.path.join(gphome, "bin"))


@then('{command} should print "{target}" to logfile')
def impl(context, command, target):
    log_dir = _get_gpAdminLogs_directory()
    filename = glob.glob('%s/%s_*.log' % (log_dir, command))[0]
    contents = ''
    with open(filename) as fr:
        for line in fr:
            contents += line
    if target not in contents:
        raise Exception("cannot find %s in %s" % (target, filename))

@given('verify that a role "{role_name}" exists in database "{dbname}"')
@then('verify that a role "{role_name}" exists in database "{dbname}"')
def impl(context, role_name, dbname):
    query = "select rolname from pg_roles where rolname = '%s'" % role_name
    conn = dbconn.connect(dbconn.DbURL(dbname=dbname))
    try:
        result = getRows(dbname, query)[0][0]
        if result != role_name:
            raise Exception("Role %s does not exist in database %s." % (role_name, dbname))
    except:
        raise Exception("Role %s does not exist in database %s." % (role_name, dbname))

@given('the system timezone is saved')
def impl(context):
    cmd = Command(name='Get system timezone',
                  cmdStr='date +"%Z"')
    cmd.run(validateAfter=True)
    context.system_timezone = cmd.get_stdout()

@then('the database timezone is saved')
def impl(context):
    cmd = Command(name='Get database timezone',
                  cmdStr='psql -d template1 -c "show time zone" -t')
    cmd.run(validateAfter=True)
    tz = cmd.get_stdout()
    cmd = Command(name='Get abbreviated database timezone',
                  cmdStr='psql -d template1 -c "select abbrev from pg_timezone_names where name=\'%s\';" -t' % tz)
    cmd.run(validateAfter=True)
    context.database_timezone = cmd.get_stdout()

@then('the database timezone matches the system timezone')
def step_impl(context):
    if context.database_timezone != context.system_timezone:
        raise Exception("Expected database timezone to be %s, but it was %s" % (context.system_timezone, context.database_timezone))

@then('the database timezone matches "{abbreviated_timezone}"')
def step_impl(context, abbreviated_timezone):
    if context.database_timezone != abbreviated_timezone:
        raise Exception("Expected database timezone to be %s, but it was %s" % (abbreviated_timezone, context.database_timezone))

@then('the startup timezone is saved')
def step_impl(context):
    logfile = "%s/pg_log/startup.log" % os.getenv("MASTER_DATA_DIRECTORY")
    timezone = ""
    with open(logfile) as l:
        first_line = l.readline()
        timestamp = first_line.split(",")[0]
        timezone = timestamp[-3:]
    if timezone == "":
        raise Exception("Could not find timezone information in startup.log")
    context.startup_timezone = timezone

@then('the startup timezone matches the system timezone')
def step_impl(context):
    if context.startup_timezone != context.system_timezone:
        raise Exception("Expected timezone in startup.log to be %s, but it was %s" % (context.system_timezone, context.startup_timezone))

@then('the startup timezone matches "{abbreviated_timezone}"')
def step_impl(context, abbreviated_timezone):
    if context.startup_timezone != abbreviated_timezone:
        raise Exception("Expected timezone in startup.log to be %s, but it was %s" % (abbreviated_timezone, context.startup_timezone))

@given("a working directory of the test as '{working_directory}'")
def impl(context, working_directory):
    context.working_directory = working_directory

def _create_cluster(context, master_host, segment_host_list):
    segment_host_list = segment_host_list.split(",")
    del os.environ['MASTER_DATA_DIRECTORY']
    os.environ['MASTER_DATA_DIRECTORY'] = os.path.join(context.working_directory,
                                                       'data/master/gpseg-1')
    try:
        with dbconn.connect(dbconn.DbURL(dbname='template1')) as conn:
            curs = dbconn.execSQL(conn, "select count(*) from gp_segment_configuration where role='m';")
            count = curs.fetchall()[0][0]
            if count == 0:
                print "Skipping creating a new cluster since the cluster is primary only already."
                return
    except:
        pass

    testcluster = TestCluster(hosts=[master_host]+segment_host_list, base_dir=context.working_directory)
    testcluster.reset_cluster()
    testcluster.create_cluster(with_mirrors=False)
    context.gpexpand_mirrors_enabled = False

@given('a cluster is created with no mirrors on "{master_host}" and "{segment_host_list}"')
def impl(context, master_host, segment_host_list):
    _create_cluster(context, master_host, segment_host_list)

@given('a cluster is created with mirrors on "{master_host}" and "{segment_host}"')
def impl(context, master_host, segment_host):
    del os.environ['MASTER_DATA_DIRECTORY']
    os.environ['MASTER_DATA_DIRECTORY'] = os.path.join(context.working_directory,
                                                       'data/master/gpseg-1')
    try:
        with dbconn.connect(dbconn.DbURL(dbname='template1')) as conn:
            curs = dbconn.execSQL(conn, "select count(*) from gp_segment_configuration where role='m';")
            count = curs.fetchall()[0][0]
            if count > 0:
                print "Skipping creating a new cluster since the cluster has mirrors already."
                return
    except:
        pass

    testcluster = TestCluster(hosts=[master_host,segment_host], base_dir=context.working_directory)
    testcluster.reset_cluster()
    testcluster.create_cluster(with_mirrors=True)
    context.gpexpand_mirrors_enabled = True

@given('the user runs gpexpand interview to add {num_of_segments} new segment and {num_of_hosts} new host "{hostnames}"')
def impl(context, num_of_segments, num_of_hosts, hostnames):
    num_of_segments = int(num_of_segments)
    num_of_hosts = int(num_of_hosts)

    hosts = []
    if num_of_hosts > 0:
        hosts = hostnames.split(',')
        if num_of_hosts != len(hosts):
            raise Exception("Incorrect amount of hosts. number of hosts:%s\nhostnames: %s" % (num_of_hosts, hosts))

    temp_base_dir = context.temp_base_dir
    primary_dir = os.path.join(temp_base_dir, 'data', 'primary')
    mirror_dir = ''
    if context.gpexpand_mirrors_enabled:
        mirror_dir = os.path.join(temp_base_dir, 'data', 'mirror')

    directory_pairs = []
    # we need to create the tuples for the interview to work.
    for i in range(0, num_of_segments):
        directory_pairs.append((primary_dir,mirror_dir))

    gpexpand = Gpexpand(context, working_directory=context.working_directory, database='gptest')
    output, returncode = gpexpand.do_interview(hosts=hosts,
                                               num_of_segments=num_of_segments,
                                               directory_pairs=directory_pairs,
                                               has_mirrors=context.gpexpand_mirrors_enabled)
    if returncode != 0:
        raise Exception("*****An error occured*****:\n %s" % output)

@given('there are no gpexpand_inputfiles')
def impl(context):
    map(os.remove, glob.glob("gpexpand_inputfile*"))

@when('the user runs gpexpand with the latest gpexpand_inputfile')
def impl(context):
    gpexpand = Gpexpand(context, working_directory=context.working_directory, database='gptest')
    gpexpand.initialize_segments()

@when('the user runs gpexpand to redistribute')
def impl(context):
    gpexpand = Gpexpand(context, working_directory=context.working_directory, database='gptest')
    context.command = gpexpand
    gpexpand.redistribute()

@when('the user runs gpexpand to redistribute with the --end flag')
def impl(context):
    gpexpand = Gpexpand(context, working_directory=context.working_directory, database='gptest')
    context.command = gpexpand
    gpexpand.redistribute(endtime=True)

@when('the user runs gpexpand to redistribute with the --duration flag')
def impl(context):
    gpexpand = Gpexpand(context, working_directory=context.working_directory, database='gptest')
    context.command = gpexpand
    gpexpand.redistribute(duration=True)

@when('the user runs gpexpand with a static inputfile for a single-node cluster with mirrors')
def impl(context):
    inputfile_contents = """sdw1:sdw1:20502:/tmp/gpexpand_behave/data/primary/gpseg2:6:2:p
sdw1:sdw1:21502:/tmp/gpexpand_behave/data/mirror/gpseg2:8:2:m
sdw1:sdw1:20503:/tmp/gpexpand_behave/data/primary/gpseg3:7:3:p
sdw1:sdw1:21503:/tmp/gpexpand_behave/data/mirror/gpseg3:9:3:m"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    inputfile_name = "%s/gpexpand_inputfile_%s" % (context.working_directory, timestamp)
    with open(inputfile_name, 'w') as fd:
        fd.write(inputfile_contents)

    gpexpand = Gpexpand(context, working_directory=context.working_directory, database='gptest')
    gpexpand.initialize_segments()

@given('the number of segments have been saved')
def impl(context):
    dbname = 'gptest'
    with dbconn.connect(dbconn.DbURL(dbname=dbname)) as conn:
        query = """SELECT count(*) from gp_segment_configuration where -1 < content"""
        context.start_data_segments = dbconn.execSQLForSingleton(conn, query)

@then('verify that the cluster has {num_of_segments} new segments')
def impl(context, num_of_segments):
    dbname = 'gptest'
    with dbconn.connect(dbconn.DbURL(dbname=dbname)) as conn:
        query = """SELECT count(*) from gp_segment_configuration where -1 < content"""
        end_data_segments = dbconn.execSQLForSingleton(conn, query)

    if int(num_of_segments) == int(end_data_segments - context.start_data_segments):
        return

    raise Exception("Incorrect amount of segments.\nprevious: %s\ncurrent: %s" % (context.start_data_segments, end_data_segments))

@given('the cluster is setup for an expansion on hosts "{hostnames}"')
def impl(context, hostnames):
    hosts = hostnames.split(",")
    temp_base_dir = context.temp_base_dir
    for host in hosts:
        cmd = Command(name='create data directories for expansion',
                      cmdStr="mkdir -p %s/data/primary; mkdir -p %s/data/mirror" % (temp_base_dir, temp_base_dir),
                      ctxt=REMOTE,
                      remoteHost=host)
        cmd.run(validateAfter=True)

@given('a temporary directory to expand into')
def impl(context):
    context.temp_base_dir = tempfile.mkdtemp(dir='/tmp')

@given('the new host "{hostnames}" is ready to go')
def impl(context, hostnames):
    hosts = hostnames.split(',')
    reset_hosts(hosts, context.working_directory)
    reset_hosts(hosts, context.temp_base_dir)

@given('the database is killed on hosts "{hostnames}"')
def impl(context, hostnames):
    hosts = hostnames.split(",")
    for host in hosts:
        cmd = Command(name='pkill postgres',
                      cmdStr="pkill postgres || true",
                      ctxt=REMOTE,
                      remoteHost=host)
        cmd.run(validateAfter=True)

@given('user has created expansionranktest tables')
def impl(context):
    dbname = 'gptest'
    with dbconn.connect(dbconn.DbURL(dbname=dbname)) as conn:
        for i in range(7,10):
            query = """drop table if exists expansionranktest%s""" % (i)
            dbconn.execSQL(conn, query)
            query = """create table expansionranktest%s(a int)""" % (i)
            dbconn.execSQL(conn, query)
        conn.commit()

@given('user has fixed the expansion order for tables')
@when('user has fixed the expansion order for tables')
def impl(context):
    dbname = 'gptest'
    with dbconn.connect(dbconn.DbURL(dbname=dbname)) as conn:
        for i in range(7,10):
            query = """UPDATE gpexpand.status_detail SET rank=%s WHERE fq_name = 'public.expansionranktest%s'""" % (i,i)
            dbconn.execSQL(conn, query)
        conn.commit()

@then('the tables were expanded in the specified order')
def impl(context):
# select rank from gpexpand.status_detail WHERE rank IN (7,8,9) ORDER BY expansion_started;
    dbname = 'gptest'
    with dbconn.connect(dbconn.DbURL(dbname=dbname)) as conn:
        query = """select rank from gpexpand.status_detail WHERE rank IN (7,8,9) ORDER BY expansion_started"""
        cursor = dbconn.execSQL(conn, query)

        rank = cursor.fetchone()[0]
        if rank != 7:
            raise Exception("Expected table with gpexpand.status rank 7 to have "
                            "started expanding first instead got table with rank "
                            "%d") % rank

        rank = cursor.fetchone()[0]
        if rank != 8:
            raise Exception("Expected table with gpexpand.status rank 8 to have "
                            "started expanding second instead got table with rank "
                            "%d") % rank

        rank = cursor.fetchone()[0]
        if rank != 9:
            raise Exception("Expected table with gpexpand.status rank 9 to have "
                            "started expanding third instead got table with rank "
                            "%d") % rank

        return

@given('an FTS probe is triggered')
def impl(context):
    with dbconn.connect(dbconn.DbURL(dbname='postgres')) as conn:
        dbconn.execSQLForSingleton(conn, "SELECT gp_request_fts_probe_scan()")

@then('verify that gpstart on original master fails due to lower Timeline ID')
def step_impl(context):
    ''' This assumes that gpstart still checks for Timeline ID if a standby master is present '''
    context.execute_steps(u'''
                            When the user runs "gpstart -a"
                            Then gpstart should return a return code of 2
                            And gpstart should print "Standby activated, this node no more can act as master." to stdout
                            ''')

@then('verify gpstate with options "{options}" output is correct')
def step_impl(context, options):
    if '-f' in options:
        if context.standby_hostname not in context.stdout_message or \
                context.standby_data_dir not in context.stdout_message or \
                str(context.standby_port) not in context.stdout_message:
            raise Exception("gpstate -f output is missing expected standby master information")
    elif '-s' in options:
        if context.standby_hostname not in context.stdout_message or \
                context.standby_data_dir not in context.stdout_message or \
                str(context.standby_port) not in context.stdout_message:
            raise Exception("gpstate -s output is missing expected master information")
    elif '-Q' in options:
        for stdout_line in context.stdout_message.split('\n'):
            if 'up segments, from configuration table' in stdout_line:
                segments_up = int(re.match(".*of up segments, from configuration table\s+=\s+([0-9]+)", stdout_line).group(1))
                if segments_up <= 1:
                    raise Exception("gpstate -Q output does not match expectations of more than one segment up")

            if 'down segments, from configuration table' in stdout_line:
                segments_down = int(re.match(".*of down segments, from configuration table\s+=\s+([0-9]+)", stdout_line).group(1))
                if segments_down != 0:
                    raise Exception("gpstate -Q output does not match expectations of all segments up")
                break ## down segments comes after up segments, so we can break here
    elif '-m' in options:
        dbname = 'postgres'
        with dbconn.connect(dbconn.DbURL(hostname=context.standby_hostname, port=context.standby_port, dbname=dbname)) as conn:
            query = """select datadir, port from pg_catalog.gp_segment_configuration where role='m' and content <> -1;"""
            cursor = dbconn.execSQL(conn, query)

        for i in range(cursor.rowcount):
            datadir, port = cursor.fetchone()
            if datadir not in context.stdout_message or \
                str(port) not in context.stdout_message:
                    raise Exception("gpstate -m output missing expected mirror info, datadir %s port %d" %(datadir, port))
    else:
        raise Exception("no verification for gpstate option given")
