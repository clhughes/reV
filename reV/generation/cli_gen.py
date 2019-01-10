"""
Generation CLI entry points.
"""
import click
import logging
from math import ceil
import os
import pprint
import re
import time

from reV import __testdatadir__
from reV.config.project_points import ProjectPoints, PointsControl
from reV.config.gen_config import GenConfig
from reV.generation.generation import Gen
from reV.utilities.cli_dtypes import INT, STR, SAMFILES, PROJECTPOINTS, INTLIST
from reV.utilities.exceptions import ConfigError
from reV.utilities.execution import PBS, SLURM, SubprocessManager
from reV.utilities.loggers import init_logger, REV_LOGGERS


logger = logging.getLogger(__name__)


def init_gen_loggers(verbose, name, logdir='./out/log',
                     modules=['reV.config', 'reV.generation',
                              'reV.utilities']):
    """Initialize multiple loggers to a single file for the gen compute."""
    if verbose:
        log_level = 'DEBUG'
    else:
        log_level = 'INFO'

    if not os.path.exists(logdir):
        os.makedirs(logdir)

    for module in modules:
        log_file = os.path.join(logdir, '{}.log'.format(name))

        # do not initialize a redundant logger
        logger = REV_LOGGERS[module]
        if 'log_file' not in logger:
            logger = init_logger(module, log_level=log_level,
                                 log_file=log_file)
    return logger


@click.group()
@click.option('--name', '-n', default='reV_gen', type=STR,
              help='Generation job name. Default is "reV_gen".')
@click.option('-v', '--verbose', is_flag=True,
              help='Flag to turn on debug logging. Default is not verbose.')
@click.pass_context
def main(ctx, name, verbose):
    """Command line interface (CLI) for the reV 2.0 Generation Module."""
    ctx.obj['NAME'] = name
    ctx.obj['VERBOSE'] = verbose


@main.command()
@click.option('--config_file', '-c', required=True, type=STR,
              help='reV generation configuration json file.')
@click.option('-v', '--verbose', is_flag=True,
              help='Flag to turn on debug logging. Default is not verbose.')
@click.pass_context
def from_config(ctx, config_file, verbose):
    """Run reV gen from a config file."""
    name = ctx.obj['NAME']
    verbose = any([verbose, ctx.obj['VERBOSE']])

    # Instantiate the config object
    config = GenConfig(config_file)

    # Enforce verbosity if logging level is specified in the config
    if config.logging_level == logging.DEBUG:
        verbose = True

    # initialize loggers. Not SAM (will be logged in the invoked processes).
    init_gen_loggers(verbose, name, logdir=config.logdir)

    # Initial log statements
    logger.info('Running reV 2.0 generation from config file: "{}"'
                .format(config_file))
    logger.info('The following project points were specified: "{}"'
                .format(config.get('project_points', None)))
    logger.info('The following SAM configs are available to this run:\n{}'
                .format(pprint.pformat(config.get('sam_generation', None),
                                       indent=4)))
    logger.debug('The full configuration input is as follows:\n{}'
                 .format(pprint.pformat(config, indent=4)))

    # set config objects to be passed through invoke to direct methods
    ctx.obj['TECH'] = config.tech
    ctx.obj['POINTS'] = config['project_points']
    ctx.obj['SAM_FILES'] = config.sam_gen
    ctx.obj['DIROUT'] = config.dirout
    ctx.obj['LOGDIR'] = config.logdir
    ctx.obj['CF_PROFILES'] = config.write_profiles
    ctx.obj['SITES_PER_CORE'] = config.execution_control['sites_per_core']

    for i, year in enumerate(config.years):
        submit_from_config(ctx, name, year, config, verbose, i)


def submit_from_config(ctx, name, year, config, verbose, i):
    """Function to submit one year from a config file.

    Parameters
    ----------
    ctx : cli.ctx
        Click context object. Use case: data = ctx.obj['key']
    name : str
        Job name.
    year : int | str
        4 digit year
    config : reV.config.GenConfig
        Generation config object.
    """
    # set the year-specific variables
    ctx.obj['RES_FILE'] = config.res_files[i]

    # if the year isn't in the name, add it before setting the file output
    match = re.match(r'.*([1-3][0-9]{3})', name)
    if not match:
        ctx.obj['FOUT'] = '{}_{}.h5'.format(name, year)

    # check to make sure that the year matches the resource file
    if str(year) not in config.res_files[i]:
        raise Exception('Resource file and year do not appear to match. '
                        'Expected the string representation of the year '
                        'to be in the resource file name. '
                        'Year: {}, Resource file: {}'
                        .format(year, config.res_files[i]))

    # invoke direct methods based on the config execution option
    if config.execution_control.option == 'local':
        sites_per_core = ceil(len(config.points_control) /
                              config.execution_control.ppn)
        ctx.obj['SITES_PER_CORE'] = sites_per_core
        ctx.invoke(local, n_workers=config.execution_control.ppn,
                   points_range=None, verbose=verbose)

    elif config.execution_control.option == 'peregrine':
        if not match:
            # 11 chars for pbs job name (lim is 16, -5 for "_yrID")
            ctx.obj['NAME'] = '{}_{}'.format(name[:11], str(year)[-2:])
        ctx.invoke(peregrine, nodes=config.execution_control.nodes,
                   alloc=config.execution_control.alloc,
                   queue=config.execution_control.queue,
                   feature=config.execution_control.feature,
                   stdout_path=os.path.join(config.dirout, 'stdout'),
                   verbose=verbose)

    elif config.execution_control.option == 'eagle':
        if not match:
            # 3 chars for slurm job name (lim is 8, -5 for "_yrID")
            ctx.obj['NAME'] = '{}_{}'.format(name[:3], str(year)[-2:])
        ctx.invoke(eagle, nodes=config.execution_control.nodes,
                   alloc=config.execution_control.alloc,
                   walltime=config.execution_control.walltime,
                   memory=config.execution_control.node_mem,
                   stdout_path=os.path.join(config.dirout, 'stdout'),
                   verbose=verbose)
    else:
        raise ConfigError('Execution option not recognized: "{}"'
                          .format(config.execution_control.option))


@main.group()
@click.option('--tech', '-t', required=True, type=STR,
              help='reV tech to analyze (required).')
@click.option('--sam_files', '-sf', required=True, type=SAMFILES,
              help='SAM config files (required) (str, dict, or list).')
@click.option('--res_file', '-rf', required=True,
              help='Single resource file (required) (str).')
@click.option('--points', '-p', default=slice(0, 100), type=PROJECTPOINTS,
              help=('reV project points to analyze '
                    '(slice, list, or file string). '
                    'Default is slice(0, 100)'))
@click.option('--sites_per_core', '-spc', default=None, type=INT,
              help=('Number of sites to run in series on a single core. '
                    'Default is the resource column chunk size.'))
@click.option('--fout', '-fo', default='gen_output.h5', type=STR,
              help=('Filename output specification (should be .h5). '
                    'Default is "gen_output.h5"'))
@click.option('--dirout', '-do', default='./out/gen_out', type=STR,
              help='Output directory specification. Default is ./out/gen_out')
@click.option('--logdir', '-lo', default='./out/log_gen', type=STR,
              help='Generation log file directory. Default is ./out/log_gen')
@click.option('-cfp', '--cf_profiles', is_flag=True,
              help=('Flag to output/save capacity factor profiles. '
                    'Default is not to save profiles.'))
@click.option('-v', '--verbose', is_flag=True,
              help='Flag to turn on debug logging. Default is not verbose.')
@click.pass_context
def direct(ctx, tech, sam_files, res_file, points, sites_per_core,
           fout, dirout, logdir, cf_profiles, verbose):
    """Run reV gen directly w/o a config file."""
    ctx.ensure_object(dict)
    ctx.obj['TECH'] = tech
    ctx.obj['POINTS'] = points
    ctx.obj['SAM_FILES'] = sam_files
    ctx.obj['RES_FILE'] = res_file
    ctx.obj['SITES_PER_CORE'] = sites_per_core
    ctx.obj['FOUT'] = fout
    ctx.obj['DIROUT'] = dirout
    ctx.obj['LOGDIR'] = logdir
    ctx.obj['CF_PROFILES'] = cf_profiles
    verbose = any([verbose, ctx.obj['VERBOSE']])


@direct.command()
@click.option('--n_workers', '-nw', type=INT,
              help=('Required input: number of workers. '
                    'Use 1 for serial, None for all cores.'))
@click.option('--points_range', '-pr', default=None, type=INTLIST,
              help='Optional range list to run a subset of sites.')
@click.option('-v', '--verbose', is_flag=True,
              help='Flag to turn on debug logging.')
@click.pass_context
def local(ctx, n_workers, points_range, verbose):
    """Run generation on local worker(s)."""

    name = ctx.obj['NAME']
    tech = ctx.obj['TECH']
    points = ctx.obj['POINTS']
    sam_files = ctx.obj['SAM_FILES']
    res_file = ctx.obj['RES_FILE']
    sites_per_core = ctx.obj['SITES_PER_CORE']
    fout = ctx.obj['FOUT']
    dirout = ctx.obj['DIROUT']
    logdir = ctx.obj['LOGDIR']
    cf_profiles = ctx.obj['CF_PROFILES']
    verbose = any([verbose, ctx.obj['VERBOSE']])

    init_gen_loggers(verbose, name, logdir=logdir)

    for key, val in ctx.obj.items():
        logger.debug('ctx var passed to local method: "{}" : "{}" with type '
                     '"{}"'.format(key, val, type(val)))

    logger.info('Gen local is being run with with job name "{}" and resource '
                'file: {}. Target output path is: {}'
                .format(name, res_file, os.path.join(dirout, fout)))
    t0 = time.time()

    # Execute the Generation module with smart data flushing.
    Gen.run_smart(tech=tech,
                  points=points,
                  sam_files=sam_files,
                  res_file=res_file,
                  cf_profiles=cf_profiles,
                  n_workers=n_workers,
                  sites_per_split=sites_per_core,
                  points_range=points_range,
                  fout=fout,
                  dirout=dirout)

    tmp_str = ' with points range {}'.format(points_range)
    logger.info('Gen compute complete for project points "{0}"{1}. '
                'Time elapsed: {2:.2f} min. Target output dir: {3}'
                .format(points, tmp_str if points_range else '',
                        (time.time() - t0) / 60, dirout))


def get_node_pc(points, sam_files, tech, res_file, nodes):
    """Get a PointsControl object to be send to HPC nodes.

    Parameters
    ----------
    points : slice | str | list | tuple
        Slice/list specifying project points, string pointing to a project
        points csv.
    sam_files : dict | str | list
        SAM input configuration ID(s) and file path(s). Keys are the SAM
        config ID(s), top level value is the SAM path. Can also be a single
        config file str. If it's a list, it is mapped to the sorted list
        of unique configs requested by points csv.
    tech : str
        reV technology being executed.
    res_file : str
        Optional resource file to find maximum length of project points if
        points slice stop is None.
    nodes : int
        Number of nodes that the PointsControl object is being split to.

    Returns
    -------
    pc : reV.config.project_points.PointsControl
        PointsControl object to be iterated and send to HPC nodes.
    """

    if isinstance(points, (str, slice, list, tuple)):
        # create points control via points
        pp = ProjectPoints(points, sam_files, tech, res_file=res_file)
        sites_per_node = ceil(len(pp) / nodes)
        pc = PointsControl(pp, sites_per_split=sites_per_node)
    else:
        raise TypeError('Generation Points input type is unrecognized: '
                        '"{}"'.format(type(points)))
    return pc


def get_node_name_fout(name, fout, i, hpc='slurm'):
    """Make a node name and fout unique to the run name, year, and node number
    Parameters
    ----------
    name : str
        Base node/job name
    fout : str
        Base file output name (no path) (with or without .h5 extension)
    i : int
        Node number.
    hpc : str
        HPC job submission tool name (e.g. slurm or pbs). Affects job name.

    Returns
    -------
    node_name : str
        Base node name with _00 tag. 16 char max.
    fout_node : str
        Base file output name with _node00 tag.
    """
    if hpc is 'slurm':
        lim = 6
    elif hpc is 'pbs':
        lim = 14

    # 14 chars for pbs, 6 for slurm (lim is 16/8, -2 for "2charID")
    node_name = '{0}{1:02d}'.format(name[:lim], i)
    if fout.endswith('.h5'):
        # remove file extension to add additional node and year strings
        fout = fout.strip('.h5')
    # add node number to file name.
    fout_node = fout + '_node{0:02d}.h5'.format(i)
    return node_name, fout_node


def get_node_cmd(name='reV', tech='pv',
                 points=slice(0, 100), points_range=None,
                 sam_files=__testdatadir__ + '/SAM/naris_pv_1axis_inv13.json',
                 res_file=__testdatadir__ + '/nsrdb/ri_100_nsrdb_2012.h5',
                 sites_per_core=None, n_workers=None, fout='reV.h5',
                 dirout='./out/gen_out', logdir='./out/log_gen',
                 cf_profiles=False, verbose=False):
    """Made a reV gen local cli call string.

    Parameters
    ----------
    name : str
        Name of the job to be submitted.
    tech : str
        Name of the reV technology to be analyzed.
        (e.g. pv, csp, landbasedwind, offshorewind).
    points : slice | str | list | tuple
        Slice/list specifying project points, string pointing to a project
    points_range : list | None
        Optional range list to run a subset of sites
    sam_files : dict | str | list
        SAM input configuration ID(s) and file path(s). Keys are the SAM
        config ID(s), top level value is the SAM path. Can also be a single
        config file str. If it's a list, it is mapped to the sorted list
        of unique configs requested by points csv.
    res_file : str
        WTK or NSRDB resource file name + path.
    sites_per_core : int | None
        Number of sites to be analyzed in serial on a single local core.
    n_workers : int | None
        Number of workers to use on a node. None defaults to all available
        workers.
    fout : str
        Target filename to dump generation outputs.
    dirout : str
        Target directory to dump generation fout.
    logdir : str
        Target directory to save log files.
    cf_profiles : bool
        Flag to save capacity factor timeseries profiles. Default is False.
    verbose : bool
        Flag to turn on debug logging. Default is False.

    Returns
    -------
    cmd : str
        Single line command line argument to call the following CLI with
        appropriately formatted arguments based on inputs:
            python -m reV.generation.cli_gen direct local
    """

    # mark a cli arg string for main() in this module
    arg_main = ('-n {name} '.format(name=SubprocessManager.s(name)))

    # make a cli arg string for direct() in this module
    arg_direct = ('-t {tech} '
                  '-p {points} '
                  '-sf {sam_files} '
                  '-rf {res_file} '
                  '-spc {sites_per_core} '
                  '-fo {fout} '
                  '-do {dirout} '
                  '-lo {logdir} '
                  '{cfp} '
                  .format(tech=SubprocessManager.s(tech),
                          points=SubprocessManager.s(points),
                          sam_files=SubprocessManager.s(sam_files),
                          res_file=SubprocessManager.s(res_file),
                          sites_per_core=SubprocessManager.s(sites_per_core),
                          fout=SubprocessManager.s(fout),
                          dirout=SubprocessManager.s(dirout),
                          logdir=SubprocessManager.s(logdir),
                          cfp='-cfp' if cf_profiles else '',
                          ))

    # make a cli arg string for local() in this module
    arg_loc = ('-nw {n_workers} '
               '-pr {points_range} '
               '{v}'.format(n_workers=SubprocessManager.s(n_workers),
                            points_range=SubprocessManager.s(points_range),
                            v='-v' if verbose else ''))

    # Python command that will be executed on a node
    cmd = ('python -m reV.generation.cli_gen '
           '{arg_main} direct {arg_direct} local {arg_loc}'
           .format(arg_main=arg_main,
                   arg_direct=arg_direct,
                   arg_loc=arg_loc))
    logger.debug('Creating the following command line call:\n\t{}'.format(cmd))
    return cmd


@direct.command()
@click.option('--nodes', '-no', default=1, type=INT,
              help='Number of Peregrine nodes. Default is 1.')
@click.option('--alloc', '-a', default='rev', type=STR,
              help='Peregrine allocation account name. Default is "rev".')
@click.option('--queue', '-q', default='short', type=STR,
              help='Peregrine target job queue. Default is "short".')
@click.option('--feature', '-l', default=None, type=STR,
              help=('Feature request. Format is "64GB" or "24core". '
                    'Default is None.'))
@click.option('--stdout_path', '-sout', default='./out/stdout', type=STR,
              help='Subprocess standard output path. Default is ./out/stdout')
@click.option('-v', '--verbose', is_flag=True,
              help='Flag to turn on debug logging. Default is not verbose.')
@click.pass_context
def peregrine(ctx, nodes, alloc, queue, feature, stdout_path, verbose):
    """Run generation on Peregrine HPC via PBS job submission."""

    name = ctx.obj['NAME']
    tech = ctx.obj['TECH']
    points = ctx.obj['POINTS']
    sam_files = ctx.obj['SAM_FILES']
    res_file = ctx.obj['RES_FILE']
    sites_per_core = ctx.obj['SITES_PER_CORE']
    fout = ctx.obj['FOUT']
    dirout = ctx.obj['DIROUT']
    logdir = ctx.obj['LOGDIR']
    cf_profiles = ctx.obj['CF_PROFILES']
    verbose = any([verbose, ctx.obj['VERBOSE']])

    init_gen_loggers(verbose, name, logdir=logdir)

    pc = get_node_pc(points, sam_files, tech, res_file, nodes)

    jobs = {}

    for i, split in enumerate(pc):
        node_name, fout_node = get_node_name_fout(name, fout, i, hpc='pbs')

        cmd = get_node_cmd(name=node_name, tech=tech,
                           points=points, points_range=split.split_range,
                           sam_files=sam_files, res_file=res_file,
                           sites_per_core=sites_per_core, n_workers=None,
                           fout=fout_node, dirout=dirout, logdir=logdir,
                           cf_profiles=cf_profiles, verbose=verbose)

        logger.info('Running reV generation on Peregrine with node name "{}" '
                    'for {} (points range: {}) with target output directory: '
                    '{}'.format(node_name, pc, split.split_range, dirout))

        # create and submit the PBS job
        pbs = PBS(cmd, alloc=alloc, queue=queue, name=node_name,
                  feature=feature, stdout_path=stdout_path)
        if pbs.id:
            click.echo('Kicked off reV generation job "{}" ({}) on Peregrine.'
                       .format(node_name, pbs.id))
        else:
            click.echo('Was unable to kick of reV generation job "{}". '
                       'Please see the stdout error messages'
                       .format(node_name))
        jobs[i] = pbs

    return jobs


@direct.command()
@click.option('--nodes', '-no', default=1, type=INT,
              help='Number of Eagle nodes. Default is 1.')
@click.option('--alloc', '-a', default='rev', type=STR,
              help='Eagle allocation account name. Default is "rev".')
@click.option('--memory', '-mem', default=96, type=INT,
              help='Eagle node memory request in GB. Default is 96')
@click.option('--walltime', '-wt', default=1.0, type=float,
              help='Eagle walltime request in hours. Default is 1.0')
@click.option('--stdout_path', '-sout', default='./out/stdout', type=STR,
              help='Subprocess standard output path. Default is ./out/stdout')
@click.option('-v', '--verbose', is_flag=True,
              help='Flag to turn on debug logging. Default is not verbose.')
@click.pass_context
def eagle(ctx, nodes, alloc, memory, walltime, stdout_path, verbose):
    """Run generation on Eagle HPC via SLURM job submission."""

    name = ctx.obj['NAME']
    tech = ctx.obj['TECH']
    points = ctx.obj['POINTS']
    sam_files = ctx.obj['SAM_FILES']
    res_file = ctx.obj['RES_FILE']
    sites_per_core = ctx.obj['SITES_PER_CORE']
    fout = ctx.obj['FOUT']
    dirout = ctx.obj['DIROUT']
    logdir = ctx.obj['LOGDIR']
    cf_profiles = ctx.obj['CF_PROFILES']
    verbose = any([verbose, ctx.obj['VERBOSE']])

    init_gen_loggers(verbose, name, logdir=logdir)

    pc = get_node_pc(points, sam_files, tech, res_file, nodes)

    jobs = {}

    for i, split in enumerate(pc):
        node_name, fout_node = get_node_name_fout(name, fout, i, hpc='slurm')

        cmd = get_node_cmd(name=node_name, tech=tech,
                           points=points, points_range=split.split_range,
                           sam_files=sam_files, res_file=res_file,
                           sites_per_core=sites_per_core, n_workers=None,
                           fout=fout_node, dirout=dirout, logdir=logdir,
                           cf_profiles=cf_profiles, verbose=verbose)

        logger.info('Running reV generation on Eagle with node name "{}" for '
                    '{} (points range: {}) with target output directory: {}'
                    .format(node_name, pc, split.split_range, dirout))

        # create and submit the SLURM job
        slurm = SLURM(cmd, alloc=alloc, memory=memory, walltime=walltime,
                      name=node_name, stdout_path=stdout_path)
        if slurm.id:
            click.echo('Kicked off reV generation job "{}" ({}) on Eagle.'
                       .format(node_name, slurm.id))
        else:
            click.echo('Was unable to kick of reV generation job "{}". '
                       'Please see the stdout error messages'
                       .format(node_name))
        jobs[i] = slurm

    return jobs


if __name__ == '__main__':
    main(obj={})
