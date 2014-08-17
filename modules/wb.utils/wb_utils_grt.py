# Copyright (c) 2009, 2014, Oracle and/or its affiliates. All rights reserved.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; version 2 of the
# License.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA
# 02110-1301  USA

import re
import sys
import subprocess
import os
import threading
import zipfile
import tempfile
import shlex

# import the wb module
from wb import DefineModule, wbinputs
# import the grt module
import grt
import mforms

from grt import log_warning
from workbench.log import log_info, log_error, log_debug

# define this Python module as a GRT module
ModuleInfo = DefineModule(name= "PyWbUtils", author= "Sun Microsystems Inc.", version="1.0")


# wb_model_utils contains a few more plugins that will get registered when imported
import wb_model_utils # noqa
import wb_catalog_utils # noqa

def get_linux_terminal_program():
    paths = os.getenv("PATH").split(":")
    if not paths:
        paths = ['/usr/bin', '/usr/local/bin', '/bin']

    for term in ["gnome-terminal", "konsole", "xterm", "rxvt"]:
        for d in paths:
            full_path = os.path.join(d, term)
            if os.path.exists(full_path):
                return full_path

    return None


@ModuleInfo.plugin('wb.tools.backupConnections', caption='Backup existing connections')
@ModuleInfo.export(grt.INT)
def backupConnections():
    user_data_dir = mforms.App.get().get_user_data_folder()
    connections_path = os.path.join(user_data_dir, 'connections.xml')
    instances_path = os.path.join(user_data_dir, 'server_instances.xml')
 
    file_chooser = mforms.newFileChooser(mforms.Form.main_form(), mforms.SaveFile)
    file_chooser.set_title('Export Connections As')
    file_chooser.set_extensions('ZIP Files (*.zip)|*.zip', 'import')
    if file_chooser.run_modal() == mforms.ResultOk:
        backup_path = file_chooser.get_path()
        if isinstance(backup_path, unicode):
            backup_path = backup_path.encode('utf-8')
        try:
            backup_file = zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED)
        except Exception:
            mforms.Utilities.show_error('Backup file creation error',
                                       'Could not create the backup file. Please check path and permissions and try '
                                       'again.', 'OK', '', '')
            return 1

        backup_file.write(connections_path, 'connections.xml')
        backup_file.write(instances_path, 'server_instances.xml')

        if mforms.Utilities.show_message('Connections saved', 'Your connections were successfully backed up to '
                                      + backup_path,
                                      'OK', '', 'Show File') == mforms.ResultOther:
            mforms.Utilities.reveal_file(backup_path)
    return 0


@ModuleInfo.plugin('wb.tools.restoreConnections', caption='Restore connections from a backup file')
@ModuleInfo.export(grt.INT)
def restoreConnections():
    def generate_unique_name(name, name_set):
        new_name = name
        idx = 1
        while True:
            if not new_name in name_set:
                return new_name
            new_name = name + ' (%d)' % idx
            idx += 1

    file_chooser = mforms.newFileChooser(mforms.Form.main_form(), mforms.OpenFile)
    file_chooser.set_title('Select a Connections Backup File')
    file_chooser.set_extensions('ZIP Files (*.zip)|*.zip', 'import')
    if file_chooser.run_modal():
        backup_path = file_chooser.get_path()
        try:
            backup_file = zipfile.ZipFile(backup_path, 'r')
            try:
                instances_file = tempfile.NamedTemporaryFile(delete=False)
                instances_file.write(backup_file.read('server_instances.xml'))
                instances_file.close()

                connections_file = tempfile.NamedTemporaryFile(delete=False)
                connections_file.write(backup_file.read('connections.xml'))
                connections_file.close()
            except KeyError, error:
                mforms.Utilities.show_error('Restore Connections Error', 'The selected file is not a valid backup file '
                                            'or the file is corrupted: %s.' % error.message,
                                            'OK', '', '')
                grt.log_error('restoreConnections', 'The selected file is not a valid backup file '
                              'or the file is corrupted: %s.' % error.message)
                return

            connections = grt.unserialize(connections_file.name)

            if not isinstance(connections, grt.List):
                mforms.Utilities.show_error('Restore Connections Error', 'The selected file is not a valid backup file '
                                            'or the file is corrupted.',
                                            'OK', '', '')
                grt.log_error('restoreConnections', 'The selected archive does not have a valid connection backup file.\n')
                return

            inserted_connections = {}
            existent_connection_names = set(conn.name for conn in grt.root.wb.rdbmsMgmt.storedConns)
            existent_connection_ids = set(conn.__id__ for conn in grt.root.wb.rdbmsMgmt.storedConns)
            duplicate_connection_count = 0
            for candidate_connection in connections:
                if candidate_connection.__id__ in existent_connection_ids:
                    duplicate_connection_count = duplicate_connection_count + 1
                    continue
                    
                candidate_connection.name = generate_unique_name(candidate_connection.name, existent_connection_names)

                existent_connection_names.add(candidate_connection.name)
                candidate_connection.owner = grt.root.wb.rdbmsMgmt
                inserted_connections[candidate_connection.__id__] = candidate_connection
                grt.root.wb.rdbmsMgmt.storedConns.append(candidate_connection)

            instances   = grt.unserialize(instances_file.name)
            
            if not isinstance(instances, grt.List):
                mforms.Utilities.show_error('Restore Connections Error', 'The selected file is not a valid backup file '
                                            'or the file is corrupted.',
                                            'OK', '', '')
                grt.log_error('restoreConnections', 'Workbench restored %i valid connections but server configuration data coul not be found or is not valid.\n' % len(connections))
                return
     
            existent_instance_names = set(instance.name for instance in grt.root.wb.rdbmsMgmt.storedInstances)
            previous_instances_conns = set()
            duplicated_instance_count = 0
            for candidate_instance in instances:
                if candidate_instance.connection.__id__ in previous_instances_conns:
                    duplicated_instance_count = duplicated_instance_count + 1
                    continue  # Skip instances whose connections are associated to previously processed instances
                previous_instances_conns.add(candidate_instance.connection.__id__)
                candidate_instance.name = generate_unique_name(candidate_instance.name, existent_instance_names)

                existent_instance_names.add(candidate_instance.name)
                new_conn = inserted_connections.get(candidate_instance.connection.__id__, None)
                candidate_instance = candidate_instance.shallow_copy()
                candidate_instance.connection = new_conn
                grt.root.wb.rdbmsMgmt.storedInstances.append(candidate_instance)

            grt.modules.Workbench.refreshHomeConnections()
            grt.modules.Workbench.saveConnections()
            grt.modules.Workbench.saveInstances()
            
            if duplicate_connection_count > 0 or duplicated_instance_count > 0:
                message = []
                message.append('Workbench detected ')
                if duplicate_connection_count > 0:
                    message.append('%i duplicated connections' % duplicate_connection_count)
                if duplicated_instance_count > 0:
                    if duplicate_connection_count > 0:
                        message.append(' and ')
                    message.append('%i duplicated instances' % duplicated_instance_count)
                message.append(', which were not restored.')
                mforms.Utilities.show_warning('Restore Connections', ''.join(message), 'OK', '', '')
            
        except zipfile.BadZipfile, error:
            mforms.Utilities.show_error('Restore Connections Error', 'The selected file is not a valid backup file '
                                        'or the file is corrupted.',
                                        'OK', '', '')
            grt.log_error('restoreConnections', 'The selected file is not a valid backup file or the file is corrupted: %s\n' % error)
        except IOError, error:
            mforms.Utilities.show_error('Restore Connections Error', 'Cannot read from file. Please check this file '
                                        'permissions and try again.',
                                        'OK', '', '')
            grt.log_error('restoreConnections', '%s\n' % str(error))
    return 0


@ModuleInfo.export(grt.STRING, grt.classes.db_mgmt_Connection)
def connectionStringFromConnection(conn):
    #<user>[:<password>]@<host>[:<port>][:<socket>]
    connstr = ""
    if conn.driver.name == "MysqlNative":
        connstr = "%s@%s:%s" % (conn.parameterValues["userName"], conn.parameterValues["hostName"], conn.parameterValues["port"])
    elif conn.driver.name == "MysqlNativeSocket":
        connstr = "%s@%s::%s" % (conn.parameterValues["userName"], conn.parameterValues["hostName"], conn.parameterValues["socket"])
    elif conn.driver.name == "MysqlNativeSSH":
        #XXX this is incomplete, need some way to encode the ssh params
        connstr = "%s@%s::%s" % (conn.parameterValues["userName"], conn.parameterValues["hostName"], conn.parameterValues["port"])
    return connstr


@ModuleInfo.export(grt.classes.db_mgmt_Connection, grt.STRING)
def connectionFromString(connstr):
    valid = False
    
    def get_driver(name):
        for d in grt.root.wb.rdbmsMgmt.rdbms[0].drivers:
            if d.name == name:
                return d
        return None
    
    # parse as a one of our connection strings
    g = re.match("(.*?)(?::(.*))?@(.*?)(?::([0-9]+|)(?::(.+|))?)?$", connstr)
    if g:
        user, password, host, port, socket = g.groups()
        valid = True
    else:
        user, password, host, port, socket = None, None, None, None, None
        # check if this is a mysql cmdline client command
        tokens = shlex.split(connstr.strip())
        if tokens:
            print tokens
            if tokens[0].endswith("mysql") or tokens[0].endswith("mysql.exe"):
                i = 1
                valid = True
                while i < len(tokens):
                    if tokens[i] == "-u":
                        i += 1
                        user = tokens[i]
                    elif tokens[i].startswith("-u"):
                        user = tokens[i][2:]
                    elif tokens[i] == "-h":
                        i += 1
                        host = tokens[i]
                    elif tokens[i].startswith("-h"):
                        host = tokens[i][2:]
                    elif tokens[i] == "-p":
                        i += 1
                        password = tokens[i]
                    elif tokens[i].startswith("-p"):
                        password = tokens[i][2:] # noqa
                    elif tokens[i] == "-P":
                        i += 1
                        port = tokens[i]
                    elif tokens[i].startswith("-P"):
                        port = tokens[i][2:]
                    elif tokens[i] == "-S":
                        i += 1
                        socket = tokens[i]
                    elif tokens[i].startswith("-S"):
                        socket = tokens[i][2:]
                    i += 1
    if valid:
        if port:
            try:
                port = int(port)
            except:
                log_warning("wb_utils", "Error parsing connstring, port value '%s' should be a number\n" % port)
                port = None
        if not port:
            port = 3306
        conn = grt.classes.db_mgmt_Connection()
        conn.owner = grt.root.wb.rdbmsMgmt
        conn.name = connstr
        if socket:
            conn.driver = get_driver("MysqlNativeSocket")
        else:
            conn.driver = get_driver("MysqlNative")

        if user:
            conn.parameterValues["userName"] = user
        if host:
            conn.parameterValues["hostName"] = host
        if port:
            conn.parameterValues["port"] = port
        if socket:
            conn.parameterValues["socket"] = socket

        hostIdentifier = conn.driver.hostIdentifierTemplate
        for key, value in conn.parameterValues.items():
            hostIdentifier = hostIdentifier.replace("%"+key+"%", str(value))
        conn.hostIdentifier = hostIdentifier

        return conn

    return None


@ModuleInfo.plugin("wb.tools.copyConnectionString", caption="Copy Connection String to Clipboard", input= [wbinputs.selectedConnection()], pluginMenu="Home/Connections")
@ModuleInfo.export(grt.INT, grt.classes.db_mgmt_Connection)
def copyConnectionString(conn):
    connstr = connectionStringFromConnection(conn)
    mforms.Utilities.set_clipboard_text(connstr)


@ModuleInfo.plugin("wb.tools.copyJDBCConnectionString", caption="Copy JDBC Connection String to Clipboard", input= [wbinputs.selectedConnection()], pluginMenu="Home/Connections")
@ModuleInfo.export(grt.INT, grt.classes.db_mgmt_Connection)
def copyJDBCConnectionString(conn):
    if conn.parameterValues.has_key("schema"):
        params = "/"+conn.parameterValues["schema"]
    else:
        params = "/"
    params += "?user=%s" % conn.parameterValues["userName"]

    if conn.driver.name == "MysqlNative":
        connstr = "jdbc:mysql://%s:%s" % (conn.parameterValues["hostName"], conn.parameterValues["port"])
    elif conn.driver.name == "MysqlNativeSocket":
        connstr = "jdbc:mysql://%s:%s" % (conn.parameterValues["hostName"], conn.parameterValues["socket"])
    elif conn.driver.name == "MysqlNativeSSH":
        mforms.Utilities.show_error("Copy JDBC Connection String",
            "Cannot create JDBC connection string for %s. The connection uses a SSH tunnel." % conn.name,
            "OK", "", "")
        return
    mforms.Utilities.set_clipboard_text(connstr+params)


@ModuleInfo.plugin("wb.tools.createMissingLocalConnections", caption="Rescan for Local MySQL Instances", input= [], pluginMenu="Home/Connections")
@ModuleInfo.export(grt.INT)
def createMissingLocalConnections():

    grt.modules.Workbench.createInstancesFromLocalServers()
    
    grt.modules.Workbench.refreshHomeConnections()
    
    return 1

@ModuleInfo.plugin("wb.tools.connectionFromClipboard", caption="Add Connection(s) from Clipboard", input= [], pluginMenu="Home/Connections")
@ModuleInfo.export(grt.INT)
def newConnectionFromClipboard():
    text = mforms.Utilities.get_clipboard_text()
    if not text:
        return 0

    existing = set()
    # make a normalized set of the connections that already exist
    for con in grt.root.wb.rdbmsMgmt.storedConns:
        existing.add(connectionStringFromConnection(con))

    parse_errors = False
    for line in text.encode("utf8").split("\n"):
        conn = connectionFromString(line)
        if not conn and not parse_errors:
            mforms.Utilities.show_error("Add Connection(s) from Clipboard", "Could not parse connection parameters from string '%s'" % line, "OK", "", "")
            parse_errors = True
            continue
        
        if connectionStringFromConnection(conn) in existing:
            mforms.Utilities.show_error("Add Connection(s) from Clipboard", "The connection %s already exists and was not added." % line,
                                        "OK", "", "")
            continue
        i = 1
        name = conn.parameterValues.get("hostName", "local")
        prefix = name
        while any(conn.name == name for conn in grt.root.wb.rdbmsMgmt.storedConns):
            name = "%s (%i)" % (prefix, i)
            i += 1
        conn.name = name
        log_info("Added connection %s from clipboard\n" % conn.name)
        grt.root.wb.rdbmsMgmt.storedConns.append(conn)

    grt.modules.Workbench.refreshHomeConnections()

    return 1


@ModuleInfo.plugin("wb.tools.cmdlineClient", caption="Start Command Line Client", input= [wbinputs.selectedConnection()], pluginMenu="Home/Connections")
@ModuleInfo.export(grt.INT, grt.classes.db_mgmt_Connection)
def startCommandLineClientForConnection(conn):
    import platform
    import os
    if "ssh" in conn.driver.name.lower():
        host = "127.0.0.1"
        tun = grt.modules.DbMySQLQuery.openTunnel(conn)
        if tun < 0:
            mforms.Utilities.show_error("Start Command Line Client", "Could not open SSH tunnel to host.", "OK", "", "")
            return
        port = grt.modules.DbMySQLQuery.getTunnelPort(tun)
        socket = ""
    elif "socket" in conn.driver.name.lower():
        if platform.system() == "Windows":
            host = "."
        else:
            host = "localhost"
        port = None
        socketName = conn.parameterValues["socket"]
        if socketName is None:
            socketName = "MySQL"
        socket = "--socket=" + socketName
    else:
        host = conn.parameterValues["hostName"].replace("\\", "\\\\").replace('"', '\\"')
        port = conn.parameterValues["port"]
        socket = ""

    user = conn.parameterValues["userName"].replace("\\", "\\\\").replace('"', '\\"')
    if port is None:
        port = 3306
    schema = conn.parameterValues["schema"]
    if schema:
        schema = schema.replace("\\", "\\\\").replace('"', '\\"')
    else:
        schema = ""

    bundled_client_path = grt.root.wb.options.options.get("mysqlclient", None)
    if platform.system().lower() == "darwin":
        if not bundled_client_path:
            bundled_client_path = mforms.App.get().get_executable_path("mysql")
        command = """\\"%s\\" \\"-u%s\\" \\"-h%s\\" -P%i %s -p %s""" % (os.path.expanduser(bundled_client_path), user, host, port, socket, schema)
        os.system("""osascript -e 'tell application "Terminal" to do script "%s"'""" % command)
    elif platform.system().lower() == "windows":
        if not bundled_client_path:
            bundled_client_path = mforms.App.get().get_executable_path("mysql.exe")
        command = """start cmd /C %s -u%s -h%s -P%i %s -p %s""" % (bundled_client_path.replace(" ", "\\ "), user, host, port, socket, schema)
        subprocess.Popen(command, shell = True)
    else:
        if not bundled_client_path:
            bundled_client_path = mforms.App.get().get_executable_path("mysql")
        if not bundled_client_path:
            bundled_client_path = "mysql"
        command = """\\"%s\\" \\"-u%s\\" \\"-h%s\\" -P%i %s -p %s""" % (bundled_client_path, user, host, port, socket, schema)
        subprocess.call(["/bin/bash", "-c", "%s -e \"%s\" &" % (get_linux_terminal_program(), command)])


if sys.platform == "linux2":
    @ModuleInfo.export(grt.INT)
    def startODBCAdmin():
        path = os.getenv('PATH')
        wb_bindir = os.getenv('MWB_BINARIES_DIR')
        if ( (wb_bindir and os.path.isfile(os.path.join(wb_bindir, 'iodbcadm-gtk'))) or
             (path and any( os.path.isfile(os.path.join(prefix, 'iodbcadm-gtk')) for prefix in path.split(':') ))
           ):
            subprocess.Popen('iodbcadm-gtk', shell=True, close_fds=True)
            return 1
        elif (path and any( os.path.isfile(os.path.join(prefix, 'ODBCManageDataSourcesQ4')) for prefix in path.split(':') )):
            subprocess.Popen('ODBCManageDataSourcesQ4', shell=True, close_fds=True)
            return 1
        else:
            return 0
elif sys.platform == "darwin":
    @ModuleInfo.export(grt.INT)
    def startODBCAdmin():
        ret = subprocess.call("open -a 'ODBC Administrator'", shell=True)
        if ret == 1:
            ret = subprocess.call("open -a 'ODBC Manager'", shell=True)
        return 0 if ret == 1 else 1
elif sys.platform == "win32":
    @ModuleInfo.export(grt.INT)
    def startODBCAdmin():
        subprocess.Popen("odbcad32.exe", shell=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        return 1


def process_not_found_utils():
    utilities_url = ("http://dev.mysql.com/downloads/utilities/" if grt.root.wb.info.edition == "Community" else
                        "https://edelivery.oracle.com/EPD/Search/get_form?product=18251")

    source_description = "www.mysql.com" if grt.root.wb.info.edition == "Community" else "eDelivery"

    if mforms.Utilities.show_message("MySQL Utilities", "The command line MySQL Utilities could not be "
                                        "located.\n\nTo use them, you must download and install the utilities "
                                        "package for your system from %s.\n\n"
                                        "Click on the Download button to proceed." % source_description,
                                "Download...", "Cancel", "") == mforms.ResultOk:

        mforms.Utilities.open_url(utilities_url)

@ModuleInfo.plugin("wb.tools.utilitiesShell", caption="Start Shell for MySQL Utilities", groups=["Others/Menu/Ungrouped"])
@ModuleInfo.export(grt.INT)
def startUtilitiesShell():
    import platform
    import os

    if platform.system() == "Windows":
        guessed_path = None
        for varname in ["ProgramFiles(x86)", "ProgramFiles"]:
            if not os.getenv(varname):
                continue
            path = os.path.join(os.getenv(varname), "MySQL", "MySQL Utilities", "mysqluc.exe")
            if os.path.exists(path):
                guessed_path = path
                break
        if any(os.path.exists(os.path.join(f, "mysqluc.exe")) for f in os.getenv("PATH").split(";")):
            # Utils path is in PATH already
            command = r'start cmd /K "mysqluc"'
            subprocess.Popen(command, shell = True)
        elif guessed_path:
            command = r'start cmd /K "%s"' % guessed_path
            subprocess.Popen(command, shell = True)
        else:
            process_not_found_utils()

    elif platform.system() == "Darwin":
        # PATH seems to be stripped down when WB is started from a binary .app
        if any(os.path.exists(f+"/mysqluc") for f in os.getenv("PATH").split(":") + ["/usr/local/bin"]):
            os.system(r"""osascript -e 'tell application "Terminal" to do script "mysqluc -e \"help utilities\""' -e 'tell front window of application "Terminal" to set custom title to "MySQL Utilities"'""")
        else:
            process_not_found_utils()
    else:
        if not any(os.path.exists(f+"/mysqluc") for f in os.getenv("PATH").split(":")):
            process_not_found_utils()
        else:
            term = get_linux_terminal_program()
            if term:
                import tempfile
                fd, setup_script = tempfile.mkstemp(prefix="delme.", dir=mforms.App.get().get_user_data_folder())
                f = os.fdopen(fd, "w+")
                f.write('echo "The following MySQL Utilities are available:"\n')
                f.write('mysqluc -e "help utilities"\n')
                f.write('rm -f "%s"\n' % setup_script)
                f.write('bash -i\n')
                f.close()
                os.chmod(setup_script, 0700)

                if 'konsole' in term:
                    subprocess.call([term, "-e", "/bin/bash", setup_script])
                else:
                    subprocess.call(["/bin/bash", "-c", "%s -e %s &" % (term, setup_script)])
            else:
                raise RuntimeError("Terminal program could not be found")


class CheckForUpdateThread(threading.Thread):
    def __init__(self):
        self.is_running = False
        self.finished = False
        super(CheckForUpdateThread, self).__init__()
    
    def run(self):
        if self.is_running:
            return
        
        self.is_running = True
        try:
            import xml.dom.minidom
            import urllib2
            
            self.dom = xml.dom.minidom.parse(urllib2.urlopen('http://wb.mysql.com/installer/products.xml'))
        except Exception, error:
            self.dom = None
            self.error = str(error)        
    
    def checkForUpdatesCallback(self):
        if self.isAlive():
            return True  # Don't do anything until the dom is built
        
        if not self.dom:
            if hasattr(self, 'error'):
                mforms.Utilities.show_error("Check for updates failed", str(self.error), "OK", "", "")
        else:
            try:
                current_version = (grt.root.wb.info.version.majorNumber, grt.root.wb.info.version.minorNumber, grt.root.wb.info.version.releaseNumber)
                edition = '' if grt.root.wb.info.license == 'GPL' else '-commercial'
                filename=u'mysql-workbench' + (edition or '-community')
                packages = ( package for package in self.dom.getElementsByTagName('Package') if package.parentNode.parentNode.attributes['name'].nodeValue == u'workbench-win32' + edition)
                version_strings = (node.attributes['thisVersion'].nodeValue for node in packages if node.attributes['filename'].nodeValue.startswith(filename) and node.attributes['filename'].nodeValue.endswith(u'msi'))
                versions = tuple( tuple( int(num) for num in version_string.split('.') ) for version_string in version_strings )
                version_list = [v for v in versions]
                newest_version = max( version_list ) if version_list else current_version
                if newest_version > current_version:
                    if mforms.Utilities.show_message('New Version Available', 'The new MySQL Workbench %s has been released.\nYou can download the latest version from\nhttp://www.mysql.com/downloads/workbench.' % '.'.join( [str(num) for num in newest_version] ),
                                                  'Get it Now', 'Maybe Later', "") == mforms.ResultOk:
                        mforms.Utilities.open_url('http://www.mysql.com/downloads/workbench')
                else:
                    mforms.Utilities.show_message('MySQL Workbench is Up to Date', 'You are already using the latest version of MySQL Workbench.', 'OK', '', '')
        
            except Exception, error:
                mforms.Utilities.show_error("Check for updates failed", str(error), "OK", "", "")

        mforms.App.get().set_status_text('Ready.')
        self.is_running = False
        self.finished = True
        return False


# Global variable:
thread = CheckForUpdateThread()

@ModuleInfo.plugin("wb.tools.checkForUpdates", caption="Check for Updates")
@ModuleInfo.export(grt.INT)
def checkForUpdates():
    global thread
    
    if thread.is_running:
        return 0
    
    if thread.finished:
        thread = CheckForUpdateThread()
    thread.start()
    mforms.App.get().set_status_text('Checking for updates...')
    ignore = mforms.Utilities.add_timeout(1.0, thread.checkForUpdatesCallback) # noqa






class SSLGenerator(mforms.Form):
    def __init__(self):
        mforms.Form.__init__(self, mforms.Form.main_form(), mforms.FormNormal)

        self.set_title("Generate SSL Certificates")

        box = mforms.newBox(False)
        box.set_padding(20)
        box.set_spacing(20)

        label = mforms.newLabel("This will generate a set of SSL certificates and other files that are required by the MySQL server to enable SSL.")
        box.add(label, False, True)

        table = mforms.newTable()
        table.set_column_count(3)
        table.set_row_count(1)

        table.set_row_spacing(8)
        table.set_column_spacing(4)

        row, self.path = self.add_label_row(table, 0, "Output Directory:", mforms.newTextEntry(), "Directory to place generated files")

        box.add(table, False, True)

        table = mforms.newTable()
        table.set_padding(12)
        table.set_column_count(3)
        table.set_row_count(7)

        table.set_row_spacing(8)
        table.set_column_spacing(4)

        row, self.country_code = self.add_label_row(table, 0, "Country:", mforms.newTextEntry(), "2 letter country code (eg, US)")
        row, self.state_name = self.add_label_row(table, row, "State or Province Name:", mforms.newTextEntry(), "Full state or province name")
        row, self.locality_name = self.add_label_row(table, row, "Locality Name:", mforms.newTextEntry(), "eg, city")
        row, self.org_name = self.add_label_row(table, row, "Organization Name:", mforms.newTextEntry(), "eg, company")
        row, self.org_unit = self.add_label_row(table, row, "Organizational Unit Name:", mforms.newTextEntry(), "eg, section, department")
        row, self.common_name = self.add_label_row(table, row, "Common Name:", mforms.newTextEntry(), "eg, put the FQDN of the server to allow server address validation")
        row, self.email_address = self.add_label_row(table, row, "Email Address:", mforms.newTextEntry(), "")

        panel = mforms.newPanel(mforms.TitledBoxPanel)
        panel.set_title("Optional Parameters")
        panel.add(table)

        box.add(panel, False, True)

        hbox = mforms.newBox(True)
        hbox.set_spacing(8)
        self.ok = mforms.newButton()
        self.ok.set_text("OK")
        self.cancel = mforms.newButton()
        self.cancel.set_text("Cancel")
        mforms.Utilities.add_end_ok_cancel_buttons(hbox, self.ok, self.cancel)

        box.add_end(hbox, False, True)

        self.set_content(box)

        self.path.focus()


    def add_label_row(self, table, row, label, control, help):
        table.add(mforms.newLabel(label, True), 0, 1, row, row+1, mforms.HFillFlag)
        table.add(control, 1, 2, row, row+1, mforms.HFillFlag|mforms.HExpandFlag)
        l = mforms.newLabel(help)
        l.set_style(mforms.SmallHelpTextStyle)
        table.add(l, 2, 3, row, row+1, mforms.HFillFlag)
        return row+1, control


    def get_attributes(self):
        l = []
        l.append("C=%s"%self.country_code.get_string_value())
        l.append("ST=%s"%self.state_name.get_string_value())
        l.append("L=%s"%self.locality_name.get_string_value())
        l.append("O=%s"%self.org_name.get_string_value())
        l.append("OU=%s"%self.org_unit.get_string_value())
        l.append("CN=%s"%self.common_name.get_string_value())
        l.append("emailAddress=%s"%self.email_address.get_string_value())
        # filter out blank values
        l = [s for s in l if s.partition("=")[-1]]
        return l


    def generate_certificate(self, tool, out_path, out_name, ca_cert, ca_key, config_file, days=3600):
        key = os.path.join(out_path, out_name+"-key.pem")
        req = os.path.join(out_path, out_name+"-req.pem")
        cert = os.path.join(out_path, out_name+"-cert.pem")

        serial_file = os.path.join(out_path, out_name+".serial")

        req_cmd = [tool, "req", "-newkey", "rsa:2048", "-days", str(days), "-nodes", "-keyout", key, "-out", req, "-config", config_file]
        log_debug("Executing %s\n" % req_cmd)
        out = subprocess.check_output(req_cmd, stderr=subprocess.STDOUT)
        log_debug("%s %s: %s\n" % (key, req, out))

        rsa_cmd = [tool, "rsa", "-in", key, "-out", key]
        log_debug("Executing %s\n" % rsa_cmd)
        out = subprocess.check_output(rsa_cmd, stderr=subprocess.STDOUT)
        log_debug("%s strip: %s\n" % (key, out))

        rsa_cmd = [tool, "x509", "-req", "-in", req, "-days", str(days), "-CA", ca_cert, "-CAkey", ca_key,
                          "-CAserial", serial_file, "-CAcreateserial",
                          "-out", cert]
        log_debug("Executing %s\n" % rsa_cmd)
        out = subprocess.check_output(rsa_cmd, stderr=subprocess.STDOUT)
        log_debug("%s strip: %s\n" % (cert, out))

        return key, req, cert


    def generate(self, path, config_file):
        days = 3600

        tool = "openssl"

        log_info("Creating CA certificate...\n")
        ca_key = os.path.join(path, "ca-key.pem")
        genrsa_cmd = [tool, "genrsa", "2048"]
        out = subprocess.check_output("%s > %s" % (" ".join(genrsa_cmd), ca_key), shell=True, stderr=subprocess.STDOUT)
        log_debug("genrsa: %s\n" % out)

        ca_cert = os.path.join(path, "ca-cert.pem")
        req_cmd = [tool, "req", "-new", "-x509", "-nodes", "-days", str(days), "-key", ca_key, "-out", ca_cert, "-config", config_file]
        log_debug("Executing %s\n" % req_cmd)
        out = subprocess.check_output(req_cmd, stderr=subprocess.STDOUT)
        log_debug("req: %s\n" % out)
        log_info("Generated CA certificate %s\n" % ca_cert)

        log_info("Create server certificate and self-sign\n")
        server_key, server_req, server_cert = self.generate_certificate(tool, path, "server", ca_cert, ca_key, config_file)
        log_info("Generated server certificate %s\n" % server_cert)

        log_info("Create client certificates and self-sign\n")
        client_key, client_req, client_cert = self.generate_certificate(tool, path, "client", ca_cert, ca_key, config_file)
        log_info("Generated client certificate %s\n" % server_cert)

        return ca_cert, server_cert, server_key, client_cert, client_key


    def run(self):
        if self.run_modal(self.ok, self.cancel):
            config_file = None
            try:
                path = self.path.get_string_value()
                try:
                    os.mkdir(path, 0700)
                except OSError, e:
                    if e.errno != 17:
                        raise

                if os.path.exists(os.path.join(path, "ca-cert.pem")):
                    if mforms.Utilities.show_warning("Generate SSL Certificates",
                                                     "Output directory %s already contains certificates, do you want to overwrite them?" % path,
                                                     "Overwrite", "Cancel", "") == mforms.ResultCancel:
                        return

                config_file = os.path.join(path, "attribs.txt")
                f = open(config_file, "w+")
                f.write("[req]\ndistinguished_name=distinguished_name\nprompt=no\n")
                f.write("\n".join(["[distinguished_name]"] + self.get_attributes())+"\n")
                f.close()

                ca_cert, server_cert, server_key, client_cert, client_key = self.generate(path, config_file)

                # write a sample my.cnf file
                sample_file = os.path.join(self.path.get_string_value(), "sample-my.cnf")
                f = open(sample_file, "w+")
                f.write("""
[client]
ssl-ca=%(ca_cert)s
ssl-cert=%(client_cert)s
ssl-key=%(client_key)s
[mysqld]
ssl-ca=%(ca_cert)s
ssl-cert=%(server_cert)s
ssl-key=%(server_key)s
""" % {"ca_cert" : ca_cert, "server_cert" : server_cert, "server_key" : server_key, "client_cert" : client_cert, "client_key" : client_key})
                f.close()

                mforms.Utilities.show_message("Generate SSL Certificates",
                                              """Certificates successfully generated.
                                                
You may now move the certificates directory to somewhere like /etc/mysql/ssl in the desired server and update the MySQL configuration.
To connect to a server using these certificates, copy the client-client.pem and client-key.pem files to a safe location in the client host.
A sample my.cnf file was written to %s.""" % sample_file,
                                              "OK", "", "")

            except Exception, e:
                import traceback
                log_error("Error generating SSL certificates: %s\n" % traceback.format_exc())
                mforms.Utilities.show_error("Generate SSL Certificates", "An error occurred while generating the certificates.\n%s" % e, "OK", "", "")
            finally:
                if config_file:
                    os.remove(config_file)



#@ModuleInfo.plugin("wb.tools.generateSSLCertificates", caption="Generate SSL Certificates...", groups=["Others/Menu/Ungrouped"])
@ModuleInfo.export(grt.INT)
def generateCertificates():
    r = SSLGenerator()
    r.run()


