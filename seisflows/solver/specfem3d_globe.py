
import subprocess

import numpy as np

import seisflows.seistools.specfem3d_globe as solvertools

from seisflows.tools import unix
from seisflows.tools.array import loadnpy, savenpy
from seisflows.tools.code import exists, glob, join, setdiff
from seisflows.tools.config import findpath, ConfigObj, ParameterObj
from seisflows.tools.io import loadbin, savebin

OBJ = ConfigObj('SeisflowsObjects')
PAR = ParameterObj('SeisflowsParameters')
PATH = ParameterObj('SeisflowsPaths')


class specfem3d_globe(object):
    """ Python interface for SPECFEM3D_GLOBE_GLOBE

      eval_func, eval_grad, apply_hess
        These methods deal with evaluation of the misfit function or its
        derivatives and provide the primary interface between the solver and
        other workflow components.

      forward, adjoint
        These methods allow direct access to individual SPECFEM3D_GLOBE components.
        Together, they provide a secondary interface users can employ for
        specialized tasks not covered by high level methods.

      prepare_solver, prepare_data, prepare_model
        SPECFEM3D_GLOBE requires a particular directory structure in which to run and
        particular file formats for models, data, and parameter files. These
        methods help put in place all these prerequisites.

      load, save
        For reading and writing SPECFEM3D_GLOBE models and kernels. On the disk,
        models and kernels are stored as binary files, and in memory, as
        dictionaries with different keys corresponding to different material
        parameters.

      split, merge
        In the solver routines, it is possible to store models as dictionaries,
        but for the optimization routines, it is necessary to merge all model
        values together into a single vector. Two methods, 'split' and 'merge',
        are used to convert back and forth between these two representations.

      combine, smooth
        Utilities for combining and smoothing kernels, meant to be called from
        external postprocessing routines.
    """

    if 0:
        # isotropic
        model_parameters = []
        model_parameters += ['reg1_rho']
        model_parameters += ['reg1_vp']
        model_parameters += ['reg1_vs']
        # model_parameters += ['reg2_rho']
        # model_parameters += ['reg2_vp']
        # model_parameters += ['reg2_vs']
        # model_parameters += ['reg3_rho']
        # model_parameters += ['reg3_vp']
        # model_parameters += ['reg3_vs']

        inversion_parameters = []
        inversion_parameters += ['reg1_rho']
        inversion_parameters += ['reg1_vp']
        inversion_parameters += ['reg1_vs']

        kernel_map = {
            'reg1_rho': 'reg1_rho_kernel',
            'reg1_vp': 'reg1_alpha_kernel',
            'reg1_vs': 'reg1_beta_kernel'}
        # 'reg2_rho':'reg2_rho_kernel',
        # 'reg2_vp':'reg2_alpha_kernel',
        # 'reg2_vs':'reg2_beta_kernel',
        # 'reg3_rho':'reg3_rho_kernel',
        # 'reg3_vp':'reg3_alpha_kernel',
        # 'reg3_vs':'reg3_beta_kernel'}

    else:
        # transversely isotropic
        model_parameters = []
        model_parameters += ['reg1_rho']
        model_parameters += ['reg1_vpv']
        model_parameters += ['reg1_vph']
        model_parameters += ['reg1_vsv']
        model_parameters += ['reg1_vsh']
        model_parameters += ['reg1_eta']
        # model_parameters += ['reg2_rho']
        # model_parameters += ['reg2_vp']
        # model_parameters += ['reg2_vs']
        # model_parameters += ['reg3_rho']
        # model_parameters += ['reg3_vp']
        # model_parameters += ['reg3_vs']

        inversion_parameters = []
        inversion_parameters += ['reg1_rho']
        inversion_parameters += ['reg1_vpv']
        inversion_parameters += ['reg1_vph']
        inversion_parameters += ['reg1_vsv']
        inversion_parameters += ['reg1_vsh']
        inversion_parameters += ['reg1_eta']

        kernel_map = {
            'reg1_rho': 'reg1_rho_kernel',
            'reg1_eta': 'reg1_rho_kernel',
            'reg1_vph': 'reg1_alpha_kernel',
            'reg1_vpv': 'reg1_alpha_kernel',
            'reg1_vsv': 'reg1_beta_kernel',
            'reg1_vsh': 'reg1_beta_kernel'}
        # 'reg2_rho':'reg2_rho_kernel',
        # 'reg2_vp':'reg2_alpha_kernel',
        # 'reg2_vs':'reg2_beta_kernel',
        # 'reg3_rho':'reg3_rho_kernel',
        # 'reg3_vp':'reg3_alpha_kernel',
        # 'reg3_vs':'reg3_beta_kernel'}

    # data channels
    channels = []
    channels += ['z']

    # data input/output
    reader = staticmethod(solvertools.read)
    writer = staticmethod(solvertools.write)


    def check(self):
        """ Checks parameters, paths, and dependencies
        """
        # check paths
        if 'GLOBAL' not in PATH:
            raise Exception

        if 'LOCAL' not in PATH:
            setattr(PATH, 'LOCAL', None)

        if 'SOLVER' not in PATH:
            if PATH.LOCAL:
                setattr(PATH, 'SOLVER', join(PATH.LOCAL, 'solver'))
            else:
                setattr(PATH, 'SOLVER', join(PATH.GLOBAL, 'solver'))

        # check dependencies
        if 'preprocess' not in OBJ:
            raise Exception

        if 'system' not in OBJ:
            raise Exception

        global preprocess
        import preprocess

        global system
        import system


    def setup(self):
        """ Prepares solver for inversion or migration

          As input for an inversion or migration, users can choose between
          supplying data or providing a target model from which data are
          generated on the fly. In both cases, all necessary SPECFEM3D_GLOBE input
          files must be provided.
        """
        unix.rm(self.event_path)

        # prepare data
        if PATH.DATA:
            self.initialize_solver_directories()
            src = glob(PATH.DATA +'/'+ self.event_name +'/'+ '*')
            dst = 'traces/obs/'
            unix.cp(src, dst)

        else:
            self.generate_data(
                model_path=PATH.MODEL_TRUE,
                model_name='model_true',
                model_type='gll')

        # prepare model
        self.generate_mesh(
            model_path=PATH.MODEL_INIT,
            model_name='model_init',
            model_type='gll')

        self.initialize_adjoint_traces()
        self.initialize_io_machinery()


    def generate_data(self, **model_kwargs):
        """ Generates data
        """
        self.generate_mesh(**model_kwargs)

        unix.cd(self.event_path)
        solvertools.setpar('SIMULATION_TYPE', '1')
        solvertools.setpar('SAVE_FORWARD', '.true.')
        self.mpirun('bin/xspecfem3D')

        unix.mv(self.wildcard, 'traces/obs')
        self.export_traces(PATH.OUTPUT, 'traces/obs')


    def generate_mesh(self, model_path=None, model_name=None, model_type='gll'):
        """ Performs meshing and database generation
        """
        assert(model_name)
        assert(model_type)

        self.initialize_solver_directories()
        unix.cd(self.event_path)

        if model_type == 'gll':
            assert (exists(model_path))
            unix.cp(glob(model_path +'/'+ '*'), self.databases)
        else:
            pass

        self.mpirun('bin/xmeshfem3D')
        self.export_model(PATH.OUTPUT +'/'+ model_name)



    ### high-level solver interface

    def eval_func(self, path='', export_traces=False):
        """ Evaluates misfit function by carrying out forward simulation and
            making measurements on observations and synthetics.
        """
        unix.cd(self.event_path)
        self.import_model(path)

        self.forward()
        unix.mv(self.wildcard, 'traces/syn')
        preprocess.prepare_eval_grad(self.event_path)

        self.export_residuals(path)
        if export_traces:
            self.export_traces(path, prefix='traces/syn')


    def eval_grad(self, path='', export_traces=False):
        """ Evaluates gradient by carrying out adjoint simulation. Adjoint traces
            must be in place prior to calling this method.
        """
        unix.cd(self.event_path)

        self.adjoint()

        self.export_kernels(path)
        if export_traces:
            self.export_traces(path, prefix='traces/syn')


    def apply_hess(self, path='', hessian='Newton'):
        """ Evaluates action of Hessian on a given model vector.
        """
        unix.cd(self.event_path)
        self.imprt(path, 'model')

        self.forward()
        unix.mv(self.wildcard, 'traces/lcg')
        preprocess.prepare_apply_hess(self.event_path)
        self.adjoint()

        self.export_kernels(path)



    ### low-level solver interface

    def forward(self):
        """ Calls SPECFEM3D_GLOBE forward solver
        """
        solvertools.setpar('SIMULATION_TYPE', '1')
        solvertools.setpar('SAVE_FORWARD', '.true.')

        self.mpirun('bin/xspecfem3D')
        unix.mv(self.wildcard, 'traces/syn')


    def adjoint(self):
        """ Calls SPECFEM3D_GLOBE adjoint solver
        """
        solvertools.setpar('SIMULATION_TYPE', '3')
        solvertools.setpar('SAVE_FORWARD', '.false.')
        unix.rm('SEM')
        unix.ln('traces/adj', 'SEM')

        self.mpirun('bin/xspecfem3D')



    ### model input/output

    def load(self, dirname, type='model'):
        """ reads SPECFEM3D_GLOBE model
        """
        if type == 'model':
            mapping = lambda key: key
        elif type == 'kernel':
            mapping = lambda key: self.kernel_map[key]
        else:
            raise ValueError

        # read database files
        parts = {}
        for key in self.model_parameters:
            parts[key] = []
            for iproc in range(PAR.NPROC):
                filename = 'proc%06d_%s.bin' % (iproc, mapping(key))
                part = loadbin(join(dirname, filename))
                parts[key].append(part)
                #if iproc==0: minval = +np.Inf # DEBUG
                #if iproc==0: maxval = -np.Inf # DEBUG
                #if part.min() < minval: minval = part.min() # DEBUG
                #if part.max() > maxval: maxval = part.max() # DEBUG
            #print key, 'min, max:', minval, maxval # DEBUG
        #print '' # DEBUG
        return parts


    def save(self, dirname, parts):
        """ writes SPECFEM3D_GLOBE model
        """
        unix.mkdir(dirname)

        # write database files
        for key in self.model_parameters:
            nn = len(parts[key])
            for ii in range(nn):
                filename = 'proc%06d_%s.bin' % (ii, key)
                savebin(parts[key][ii], join(dirname, filename))



    ### vector/dictionary conversion

    def merge(self, parts):
        """ merges dictionary into vector
        """
        v = np.array([])
        for key in self.inversion_parameters:
            for iproc in range(PAR.NPROC):
                v = np.append(v, parts[key][iproc])
        return v


    def split(self, v):
        """ splits vector into dictionary
        """
        parts = {}
        nrow = len(v)/(PAR.NPROC*len(self.inversion_parameters))
        j = 0
        for key in self.model_parameters:
            parts[key] = []
            if key in self.inversion_parameters:
                for i in range(PAR.NPROC):
                    imin = nrow*PAR.NPROC*j + nrow*i
                    imax = nrow*PAR.NPROC*j + nrow*(i + 1)
                    i += 1
                    parts[key].append(v[imin:imax])
                j += 1
            else:
                for i in range(PAR.NPROC):
                    proc = '%06d' % i
                    parts[key].append(
                        np.load(PATH.GLOBAL +'/'+ 'mesh' +'/'+ key +'/'+ proc))
        return parts



    ### postprocessing utilities

    def combine(self, path=''):
        """ combines SPECFEM3D_GLOBE kernels
        """
        dirs = unix.ls(path)

        # initialize kernels
        for key in self.model_parameters:
            if key not in self.inversion_parameters:
                for i in range(PAR.NPROC):
                    proc = '%06d' % i
                    name = self.kernel_map[key]
                    src = PATH.MESH +'/'+ key +'/'+ proc
                    dst = path +'/'+ 'sum' +'/'+ 'proc'+proc+'_'+name+'.bin'
                    savebin(np.load(src), dst)

        # create temporary files and directories expected by xsum_kernels
        unix.cd(self.event_path)
        with open('kernels_list.txt', 'w') as file:
            file.write('\n'.join(dirs) + '\n')
        unix.mkdir('INPUT_KERNELS')
        unix.mkdir('OUTPUT_SUM')
        for dir in dirs:
            src = path +'/'+ dir
            dst = unix.pwd() +'/'+ 'INPUT_KERNELS' +'/'+ dir
            unix.ln(src, dst)

        # sum kernels
        self.mpirun(PATH.SOLVER_BINARIES +'/'+ 'xsum_kernels')
        unix.mv('OUTPUT_SUM', path +'/'+ 'sum')

        # remove temporary files and directories
        unix.rm('INPUT_KERNELS')
        unix.rm('kernels_list.txt')

        unix.cd(path)


    def smooth(self, path='', tag='grad', span=0):
        """ smooths SPECFEM3D_GLOBE kernels
        """
        unix.cd(self.event_path)

        unix.mv(path +'/'+ tag, path +'/'+ tag + '_nosmooth')
        unix.mkdir(path +'/'+ tag)

        # prepare list
        kernel_list = []
        for key in self.model_parameters:
            if key in self.inversion_parameters:
                smoothing_flag = True
            else:
                smoothing_flag = False
            kernel_list = kernel_list + [[key, smoothing_flag]]

        for kernel_name, smoothing_flag in kernel_list:
            if smoothing_flag:
                # run smoothing
                print ' smoothing', kernel_name
                self.mpirun(
                    PATH.SOLVER_BINARIES +'/'+ 'xsmooth_sem '
                    + str(span) + ' '
                    + str(span) + ' '
                    + kernel_name + ' '
                    + path +'/'+ tag + '_nosmooth/' + ' '
                    + path +'/'+ tag +'/'+ ' ')
            else:
                src = glob(path +'/'+ tag+'_nosmooth/*' + kernel_name+'.bin')
                dst = path +'/'+ tag + '/' 
                unix.cp(src, dst)

        unix.rename('_smooth', '', glob(path +'/'+ tag + '/*_smooth.bin'))
        print ''

        unix.cd(path)



    ### file transfer utilities

    def import_model(self, path):
        src = glob(join(path, 'model', '*'))
        dst = self.databases
        unix.cp(src, dst)

    def export_model(self, path):
        if system.getnode() == 0:
            for name in self.model_parameters:
                src = glob(join(self.databases, '*_'+name+'.bin'))
                dst = path
                unix.mkdir(dst)
                unix.cp(src, dst)

    def export_kernels(self, path):
        try:
            unix.mkdir(join(path, 'kernels'))
        except:
            pass
        unix.mkdir(join(path, 'kernels', self.event_name))
        kernel_names = self.kernel_map.values()
        for name in kernel_names:
            src = join(glob(self.databases  +'/'+ '*'+ name+'.bin'))
            dst = join(path, 'kernels', self.event_name)
            unix.mv(src, dst)
        try:
            name = 'rhop_kernel'
            src = join(glob(self.databases +'/'+ '*'+ name+'.bin'))
            dst = join(path, 'kernels', self.event_name)
            unix.mv(src, dst)
        except:
            pass

    def export_residuals(self, path):
        try:
            unix.mkdir(join(path, 'residuals'))
        except:
            pass
        src = join(unix.pwd(), 'residuals')
        dst = join(path, 'residuals', self.event_name)
        unix.mv(src, dst)

    def export_traces(self, path, prefix='traces/obs'):
        try:
            unix.mkdir(join(path, 'traces'))
        except:
            pass
        src = join(unix.pwd(), prefix)
        dst = join(path, 'traces', self.event_name)
        unix.cp(src, dst)


    ### setup utilities

    def initialize_solver_directories(self):
        """ Creates directory structure expected by SPECFEM3D_GLOBE, copies 
          executables, and prepares input files. Executables must be supplied 
          by user as there is currently no mechanism to automatically compile 
          from source.
        """
        unix.mkdir(self.event_path)
        unix.cd(self.event_path)

        # create directory structure
        unix.mkdir('bin')
        unix.mkdir('DATA')

        unix.mkdir('traces/obs')
        unix.mkdir('traces/syn')
        unix.mkdir('traces/adj')

        unix.mkdir(self.databases)

        # copy exectuables
        src = glob(PATH.SOLVER_BINARIES +'/'+ '*')
        dst = 'bin/'
        unix.cp(src, dst)

        # copy input files
        src = glob(PATH.SOLVER_FILES +'/'+ '*')
        dst = 'DATA/'
        unix.cp(src, dst)

        #src = 'DATA/CMTSOLUTION_' + self.event_name
        #dst = 'DATA/CMTSOLUTION'
        #unix.cp(src, dst)


    def initialize_adjoint_traces(self):
        """ Adjoint traces must be initialized by writing zeros for all 
          components. This is because when reading traces at the start of an
          adjoint simulation, SPECFEM3D_GLOBE expects that all components exist.
          Components actually in use during an inversion or migration will
          be overwritten with nonzero values later on.
        """
        _, h = preprocess.load('traces/obs')
        zeros = np.zeros((h.nt, h.nr))
        for channel in ['x', 'y', 'z']:
            self.writer(zeros, h, channel=channel, prefix='traces/adj')


    def initialize_io_machinery(self):
        """ Writes mesh files expected by input/output methods
        """
        if system.getnode() == 0:

            model_set = set(self.model_parameters)
            inversion_set = set(self.inversion_parameters)

            parts = self.load(PATH.MODEL_INIT)
            try:
                path = PATH.GLOBAL +'/'+ 'mesh'
            except:
                raise Exception
            if not exists(path):
                for key in setdiff(model_set, inversion_set):
                    unix.mkdir(path +'/'+ key)
                    for proc in range(PAR.NPROC):
                        with open(path +'/'+ key +'/'+ '%06d' % proc, 'w') as file:
                            np.save(file, parts[key][proc])

            try:
                path = PATH.OPTIMIZE +'/'+ 'm_new'
            except:
                return
            if not exists(path):
                savenpy(path, self.merge(parts))
            #if not exists(path):
            #    for key in inversion_set:
            #        unix.mkdir(path +'/'+ key)
            #        for proc in range(PAR.NPROC):
            #            with open(path +'/'+ key +'/'+ '%06d' % proc, 'w') as file:
            #                np.save(file, parts[key][proc])


    ### input file writers

    def write_parameters(self):
        unix.cd(self.event_path)
        write_parameters(vars(PAR))

    def write_receivers(self):
        unix.cd(self.event_path)
        key = 'use_existing_STATIONS'
        val = '.true.'
        solvertools.setpar(key, val)
        _, h = preprocess.load('traces/obs')
        write_receivers(h.nr, h.rx, h.rz)

    def write_sources(self):
        unix.cd(self.event_path)
        _, h = preprocess.load(dir='traces/obs')
        write_sources(vars(PAR), h)


    ### utility functions

    def mpirun(self, script, output='/dev/null'):
        """ Wrapper for mpirun
        """
        with open(output,'w') as f:
            subprocess.call(
                system.mpiargs() + script,
                shell=True,
                stdout=f)

    @property
    def event_list(self):
        return NotImplementedError

    @ property
    def event_name(self):
        return '%06d' % system.getnode()

    @property
    def event_path(self):
        return join(PATH.SOLVER, self.event_name)

    @property
    def wildcard(self):
        return glob('OUTPUT_FILES/*.sem.ascii')

    @property
    def databases(self):
        return join(self.event_path, 'OUTPUT_FILES/DATABASES_MPI')


