"""
Logging for reV
"""
import logging

__all__ = ['setup_logger', 'LoggingAttributes', 'init_logger']

FORMAT = '%(levelname)s - %(asctime)s [%(filename)s:%(lineno)d] : %(message)s'
LOG_LEVEL = {'INFO': logging.INFO,
             'DEBUG': logging.DEBUG,
             'WARNING': logging.WARNING,
             'ERROR': logging.ERROR,
             'CRITICAL': logging.CRITICAL}


def get_handler(log_file=None, log_format=FORMAT):
    """
    get logger handler

    Parameters
    ----------
    log_file : str
        path to the log file
    log_format : str
        format string to use with the logging package

    Returns
    -------
    handler : logging.FileHandler | logging.StreamHandler
        handler to add to logger
    """
    if log_file:
        handler = logging.FileHandler(log_file, mode='a')
    else:
        handler = logging.StreamHandler()

    if log_format:
        logformat = logging.Formatter(log_format)
        handler.setFormatter(logformat)

    return handler


def setup_logger(logger_name, log_level="INFO", log_file=None,
                 log_format=FORMAT):
    """
    Setup logging instance with given name and attributes

    Parameters
    ----------
    logger_name : str
        Name of logger
    log_level : str
        Level of logging to capture, must be key in LOG_LEVEL
    log_file : str | list
        Path to file to use for logging, if None use a StreamHandler
        list of multiple handlers is permitted
    log_format : str
        Format for loggings, default is FORMAT

    Returns
    -------
    logger : logging.logger
        instance of logger for given name, with given level and added handler
    handler : logging.FileHandler | logging.StreamHandler | list
        handler(s) added to logger
    """
    logger = logging.getLogger(logger_name)
    current_handlers = [str(h) for h in logger.handlers]

    logger.setLevel(LOG_LEVEL[log_level])
    handlers = []
    if isinstance(log_file, list):
        for h in log_file:
            handlers.append(get_handler(log_file=h, log_format=log_format))

    else:
        handlers.append(get_handler(log_file=log_file, log_format=log_format))

    for handler in handlers:
        if str(handler) not in current_handlers:
            logger.addHandler(handler)

    return logger


class LoggingAttributes:
    """
    Class to store and pass logging attributes to modules
    """
    def __init__(self):
        self._loggers = {}

    def __setitem__(self, logger_name, attributes):
        log_attrs = self[logger_name]
        for attr, value in attributes.items():
            if attr == 'log_file':
                handlers = list(log_attrs['log_file'])
                if value not in handlers:
                    handlers.append(value)
                    log_attrs[attr] = handlers
            else:
                log_attrs[attr] = value

        self._loggers[logger_name] = log_attrs

    def __getitem__(self, logger_name):
        return self._loggers.get(logger_name, {})

    def init_logger(self, logger_name):
        """
        Extract logger attributes and initialize logger
        """
        try:
            attrs = self[logger_name]
            setup_logger(logger_name, **attrs)
        except KeyError:
            pass


REV_LOGGERS = LoggingAttributes()


def init_logger(logger_name, **kwargs):
    """
    Starts logging instance and adds logging attributes to REV_LOGGERS

    Parameters
    ----------
    logger_name : str
        Name of logger to initialize
    **kwargs
        Logging attributes used to setup_logger

    Returns
    -------
    logger : logging.logger
        logging instance that was initialized
    """
    logger = setup_logger(logger_name, **kwargs)

    REV_LOGGERS[logger_name] = kwargs

    return logger
