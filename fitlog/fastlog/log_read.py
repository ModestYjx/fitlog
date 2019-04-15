

import os
import re
import json
from collections import defaultdict
import threading
import time

class LogReader:
    def __init__(self):
        self._log_dir = None
        self._ignore_null_loss_or_metric = True  # 如果loss和metric都是null的话，则忽略
        """
        _line_counter里面的内容:{
            save_log_dir: {filename: (line_count, last_change_time)
        }
        """
        self._line_counter = defaultdict(lambda : None) # 记住每个log读取到的line的数量以及修改时间

    def set_log_dir(self, log_dir):
        if not os.path.isdir(log_dir):
            raise RuntimeError("`{}` is not a valid directory.".format(log_dir))
        empty = True
        for _dir in os.listdir(log_dir):
            if is_dirname_log_record(os.path.join(log_dir, _dir)):
                empty = False
        if empty:
            raise RuntimeError("`{}` has no valid logs.".format(log_dir))

        self._log_dir = log_dir
        self._line_counter.clear()  # 删除记录，使得重新读取

    def read_logs(self, ignore_log_names=None):
        """

        :param ignore_log_names: {}, 如果包含在这个里面，就不会读取该log
        :return: logs: [], 如果有内容或者有更新的内容，则用logs返回，每个里面都是nested的dict
        """
        assert self._log_dir is not None, "You have to set log_dir first."
        if ignore_log_names is None:
            ignore_log_names = {}
        dirs = os.listdir(self._log_dir)
        logs = []
        for _dir in dirs:
            if _dir in ignore_log_names:
                continue
            dir_path = os.path.join(self._log_dir, _dir)
            if is_dirname_log_record(dir_path):
                _dict, file_stats = _read_save_log(dir_path, self._ignore_null_loss_or_metric,
                                                   self._line_counter[_dir])
                if len(_dict) != 0:
                    logs.append({'id': _dir, **_dict})
                    self._line_counter[_dir] = file_stats
        return logs

def _read_save_log(_save_log_dir, ignore_null_loss_or_metric=True, file_stats=None):
    """
    给定一个包含metric.log, hyper.log, meta.log以及other.log的文件夹，返回一个包含数据的dict. 如果为null则返回空字典
        不读取loss.log, 因为里面的内容对table无意义。
    :param _save_log_dir: 从哪里读取log
    :param ignore_null_loss_or_metric: 是否metric为空的文件
    :param file_stats: {'meta.log': [current_line, last_modified_time],
                        'hyper.log':[], 'metric.log':[], 'other.log':[]}
    :return:
    """
    try:
        filenames = ['meta.log', 'hyper.log', 'metric.log', 'other.log']
        if file_stats is None:
            file_stats = {}
        for filename in filenames:
            if filename not in file_stats:
                file_stats[filename] = [-1, -1]
        empty = True
        _dict = {}
        with open(os.path.join(_save_log_dir, 'metric.log'), 'r', encoding='utf-8') as f:
            for line in f:
                if len(line.strip())!=0:
                    empty = False
                    break
        with open(os.path.join(_save_log_dir, 'loss.log'), 'r', encoding='utf-8') as f:
            for line in f:
                if len(line.strip())!=0:
                    empty = False
                    break

        if empty and ignore_null_loss_or_metric:
            return _dict, file_stats

        for filename in filenames:
            filepath = os.path.join(_save_log_dir, filename)
            last_modified_time = os.path.getmtime(filepath)
            if file_stats[filename][1]==last_modified_time:
                continue
            file_stats[filename][1] = last_modified_time
            start_line = file_stats[filename][0]
            __dict, end_line = _read_nonstep_log_file(filepath, start_line)
            file_stats[filename][0] = end_line
            _dict = merge(_dict, __dict, use_b=False) # 在这里，需要以文件指定顺序，保留靠前的内容的值
    except Exception as e:
        print("Exception raised when read {}".format(os.path.abspath(filepath)))
        raise e
    return _dict, file_stats


def is_log_dir_has_step(_save_log_dir):
    """

    :param _save_log_dir: str, 给定log_dir, 判断是否有step数据
    :return: bool
    """
    if not is_dirname_log_record(_save_log_dir):
        return False
    try:
        filenames = ['loss.log', 'metric.log']
        for filename in filenames:
            filepath = os.path.join(_save_log_dir, filename)
            if not os.path.exists(filepath):
                continue
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('Step:'):
                        return True
    except Exception as e:
        print("Exception raised when read {}".format(os.path.abspath(filepath)))
        return False
    return False

def _read_nonstep_log_file(filepath, start_line=0):
    """
    给定一个filepath, 读取里面非Step: 开头的line，没一行为json，使用后面的内容覆盖前面的内容
    :param filepath: str
    :param start_line: int, 从哪一行开始读取
    :return: dict.没有内容为空
    """
    a = {}
    with open(filepath, 'r', encoding='utf-8') as f:
        index = -1
        for index, line in enumerate(f):
            if index<start_line:
                continue
            if not line.startswith('Step:'): # 读取非step的内容
                line = line.strip()
                try:
                    b = json.loads(line)
                except:
                    print("Corrupted json format in {}. line:{}".format(filepath, line))
                    continue
                a = merge(a, b, use_b=True)
    return a, index+1


def merge(a, b, path=None, use_b=True):
    "merges b into a"
    # 将两个dict recursive合并到a中，有相同key的，根据use_b判断使用哪个值
    if path is None: path = []
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge(a[key], b[key], path + [str(key)])
            elif use_b:
                a[key] = b[key]
        else:
            a[key] = b[key]
    return a


def is_dirname_log_record(dir_path):
    """
    检查dir_path是否是一个合法的log目录。里面必须包含meta.log
    :param dir_path:
    :return:
    """
    if not os.path.isdir(dir_path):
        return False
    if len(re.findall('log_\d+_\d+$', dir_path))!=0:
        filenames = ['meta.log'] # 至少要有meta.log表明这个是合法的log
        for filename in filenames:
            if not os.path.exists(os.path.join(dir_path, filename)):
                return False
        return True
    else:
        return False

def is_log_record_finish(save_log_dir):
    if is_dirname_log_record(save_log_dir):
        with open(os.path.join(save_log_dir, 'meta.log'), 'r', encoding='utf-8') as f:
            line = ''
            for line in f:
                pass
            if len(line.strip()) != 0:
                try:
                    _d = json.loads(line)
                except:
                    return False
                if 'state' in _d['meta'] and _d['meta']['state']=='finish':
                    return True
    return False

class StandbyStepLogReader(threading.Thread):
    def __init__(self, save_log_dir, uuid, wait_seconds=60):
        """

        :param save_log_dir: path, where to read updating logs
        :param uuid: str, used to recognize reader
        :param wait_seconds: int, how many seconds to wait until all files are closed.
        """
        super().__init__()

        self.save_log_dir = save_log_dir
        self._file_handlers = {}

        self.uuid = uuid
        self._last_access_time = None  # 如果这么长时间没有读取到新的数据，就认为是不需要再读取的了
        # 如果这么长时间没有再次调用，就关掉文件
        self._wait_seconds = wait_seconds

        self.unfinish_lines = {} # 防止读写冲突, key: line
        self._stop_flag = False
        self._quit = False
        self._no_update_count = 0
        #TODO 修改更大一些
        self.max_no_update = 30

    def _create_file_handler(self):
        """
        检查是否有未加入的handler，加入进来
        :return:
        """
        for filename in ['metric.log', 'loss.log']:
            handler_name = filename.split('.')[0]
            if handler_name in self._file_handlers:
                continue
            filepath = os.path.join(self.save_log_dir, filename)
            handler = open(filepath, 'r', encoding='utf-8')
            self._file_handlers[handler_name] = handler

    def read_update(self, only_once=False):
        """
        调用这个函数，获取新的更新
        :param only_once: 不会再次读取内容的
        :return: 返回{loss: [dict('step':x, key:value, 'loss':{})],
                metric:[dict('step':x, key:value, 'metric':)]}
        """
        if not self._quit:
            flag = False
            if self._last_access_time is None:
                flag = True
            self._last_access_time = time.time()
            self._create_file_handler()
            updates = defaultdict(list)
            for filename, handler in self._file_handlers.items():
                for line in handler.readlines():
                    if filename in self.unfinish_lines:
                        line = self.unfinish_lines.pop(filename) + line
                    if not line.endswith('\n'):# 结尾不是回车，说明没有读完
                        self.unfinish_lines[filename] = line
                    else:
                        if line.startswith('Step:'):
                            line = line[line.index('\t')+1:].strip()
                            _dict = json.loads(line)
                            updates[filename].append(_dict)
                if len(updates[filename])!=0:  # 对step排序，保证不要出现混乱
                    updates[filename] = updates[filename].sort(key=lambda x:x['step'])
            if not only_once:
                if len(updates)==0:
                    self._no_update_count += 1
                else:
                    self._no_update_count = 0
                if flag:
                    self.start()
            else: # 如果确定只读一次，则直接关闭。应该是finish了
                self._close_file_handler()
                updates['finish'] = True
        if self._quit or self._no_update_count>self.max_no_update:
            updates = {'finish': True}
            self._quit = True
            self.stop()
        return updates

    def _close_file_handler(self):
        for key, handler in self._file_handlers.items():
            handler.close()
        self._file_handlers.clear()

    def stop(self):
        """
        如果手动停止某个任务
        :return:
        """
        self._stop_flag = True
        self._close_file_handler()
        count = 0
        while not self._quit:
            time.sleep(1)
            if count>3:
                raise RuntimeError("Multi-thread bug here. It should not run twice.")
            count += 1

    def run(self):
        while time.time() - self._last_access_time<self._wait_seconds and not self._stop_flag and \
                self._no_update_count<self.max_no_update:
            time.sleep(0.5)
        self._quit = True
        self._close_file_handler()
        print("The updating for {}:{} finished.".format(os.path.basename(self.save_log_dir), self.uuid))
