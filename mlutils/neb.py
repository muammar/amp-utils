# This module was written by Muammar El Khatib <muammar@brown.edu>

# General imports
from sklearn.metrics import mean_absolute_error
import subprocess
import os.path
import copy
import numpy as np

# ASE imports
from ase.io import read, Trajectory
from ase.neb import SingleCalculatorNEB as NEB

# Amp imports
from amp import Amp


class accelerate_neb(object):
    """Accelerating NEB calculations using Machine Learning

    This class accelerates NEB calculations using an algorithm proposed by
    Peterson, A. A. (2016). Acceleration of saddle-point searches with machine
    learning. The Journal of Chemical Physics, 145(7), 74106.
    https://doi.org/10.1063/1.4960708

    Parameters
    ----------
    initial : str
        Path to the initial image.
    final : str
        Path to the final image.
    maxiter : int
        Number of maximum neb calls to find defined tolerance.
    tolerance : float
        Set the maximum error you expect from the model. The lower the less
        error.
    fmax : float
        The fmax used by .run() method in the NEB class available in ASE.
    ifmax : float
        Initial fmax. Useful when your starting training set is too poor and
        then you ask for a NEB with a larger fmax that can be easily obtained.
        In this way training set improves faster but the whole process is
        slower (you do more DFT calculations).
    step : float
        Useful to help the convergence. This number divides ifmax.
    logfile : str
        Path to create logfile.
    metric : str
        By default the metric is fmax as computed in ase/neb.py (recommended).
        Other possibility is MAE. See cross_validate method in this class.
    maxrunsteps : int
        Maximum number of times that .run will execute a band optimization.
    previous_nebfile : bool
        Whether or not we will restart the process from a previous iteration.
    """
    def __init__(self, initial=None, final=None, tolerance=0.01, maxiter=200,
                 fmax=0.05, ifmax=None, logfile=None, step=None,
                 maxrunsteps=None, previous_nebfile=False, metric='fmax'):

        if logfile is None:
            logfile = 'acceleration.log'

        self.initialized = False
        self.trained = False
        self.maxiter = maxiter
        self.tolerance = tolerance
        self.fmax = fmax
        self.step = step
        self.final_fmax = False
        self.maxrunsteps = maxrunsteps
        self.previous_nebfile = previous_nebfile
        self.metric = metric

        if ifmax is None:
            self.ifmax = fmax
        else:
            self.ifmax = ifmax

        if os.path.isfile(logfile):
            self.logfile = open(logfile, 'a')
        else:
            self.logfile = open(logfile, 'w')

        if initial is not None and final is not None:
            self.initial = read(initial)
            self.final = read(final)
        else:
            self.logfile.write('You need to an initial and final states.')

    def initialize(self, calc=None, amp_calc=None, climb=False,
                   intermediates=None, restart=False, cores=None,
                   neb_optimizer='BFGS'):
        """Method to initialize the acceleration of NEB

        Parameters
        ----------
        calc : object or str
            This is the calculator used to perform DFT calculations. GPAW is
            weird and you have to pass calc as a string or None because we will
            operate differently with it.
        amp_calc : object
            This is the machine learning model used to perform predictions.
        intermediates : int
            Number of intermediate images for the NEB calculation.
        climb : bool
            Whether or not NEB will be run using climbing image mode.
        restart : bool
            Restart a calculation.
        neb_optimizer : str
            Optimizer used by NEB.
        """
        self.calc = calc
        self.cores = cores
        self.neb_optimizer = neb_optimizer
        if restart is None:
            self.logfile.write('NEB acceleration initialized\n')
        else:
            self.logfile.write('NEB acceleration restarted\n')

        if self.cores is not None:
            self.logfile.write('Number of cores for GPAW calculator is %s \n'
                               % self.cores)
            self.logfile.flush()

        self.logfile.write('The optimizer used for the NEB calculation is %s'
                           '\n' % self.neb_optimizer)

        self.logfile.write('The metric chosen is %s \n' % self.metric)

        if calc is None:
            self.calc_name = 'GPAW'
        else:
            self.calc_name = self.calc.__class__.__name__

        self.amp_calc = amp_calc
        self.intermediates = intermediates
        self.initialized = restart
        # This is the total number of images in NEB
        self.nreadimg = (self.intermediates + 2)

        if self.initialized is False:
            images = [self.initial]
            for intermediate in range(self.intermediates):
                image = self.initial.copy()
                image.set_calculator(self.calc)
                images.append(image)

            images.append(self.final)

            # When using something different from GPAW, we need to write the
            # images to file, and then read them back.
            if self.calc_name != 'GPAW':
                self.neb_images = self.run_neb(images, interpolate=True)
            else:
                self.run_neb(images, interpolate=True)
                self.neb_images = read('training.traj',
                                       index=slice(0, self.nreadimg))

            self.initialized = True

        elif self.initialized is True:
            nreadimg = -(self.intermediates + 2)
            self.neb_images = read('training.traj',
                                   index=slice(0, self.nreadimg))

            # We check the existance of previous files and we restart the
            # calculations. #FIXME this is not probably finished for all cases.

            nebfile, digit = check_files()

            if nebfile is not False:
                """
                This case only requires crossvalidation
                """
                self.training_set = Trajectory('training.traj')
                self.iteration = int(digit)
                self.logfile.write('Restarting at iteration %s \n'
                                   % self.iteration)
                self.logfile.write('Using NEB trajectory %s \n' % nebfile)
                self.logfile.flush()
                self.trained = True
                new_neb_images = read(nebfile, index=slice(nreadimg, None))
                newcalc = Amp.load('%s.amp' % self.iteration)
                self.achieved = self.cross_validate(new_neb_images,
                                                    calc=self.calc,
                                                    amp_calc=newcalc,
                                                    metric=self.metric)
                clean_dir(logfile=self.logfile)
                del newcalc
                self.logfile.flush()

            elif nebfile is False:
                self.training_set = Trajectory('training.traj')
                try:
                    self.iteration = int(digit)
                    self.logfile.write('Restarting at iteration %s \n'
                                       % self.iteration)
                    self.logfile.write('Using calc %s.amp \n' % digit)
                    self.logfile.flush()
                    self.trained = True
                    self.neb_images = read('training.traj',
                                           index=slice(0, self.nreadimg))
                    self.logfile.write('Starting ML-NEB calculation... '
                                       'Go, and grab a cup of coffee :) \n')
                    self.logfile.flush()
                    newcalc = Amp.load('%s.amp' % digit)
                    calc_name = newcalc.__class__.__name__

                    if self.previous_nebfile is False:
                        self.neb_images = read('training.traj',
                                               index=slice(0, self.nreadimg))
                    else:
                        nebfile = 'neb_%s.traj' % (digit - 1)
                        self.neb_images = read(nebfile,
                                               index=slice(-self.nreadimg,
                                                           None))
                    images = self.set_calculators(
                            self.neb_images,
                            newcalc,
                            calc_name=calc_name,
                            logfile=self.logfile,
                            cores=self.cores
                            )
                    self.run_neb(self.neb_images, fmax=self.fmax)
                    del newcalc
                    clean_dir(logfile=self.logfile)
                    self.logfile.write('ML-NEB calculation finished... \o/ \n')

                    # We now read the last images from the NEB: initial,
                    # intermediate, and final states.
                    ini_neb_images = read(self.traj,
                                          index=slice(nreadimg, None))
                    self.logfile.write('New guessed ML-MEP was read from %s \n'
                                       % self.traj)
                    self.logfile.flush()

                    newcalc = Amp.load('%s.amp' % label)
                    self.achieved = self.cross_validate(ini_neb_images,
                                                        calc=self.calc,
                                                        amp_calc=newcalc,
                                                        metric=self.metric)
                    clean_dir(logfile=self.logfile)
                    del newcalc
                    self.logfile.flush()
                    self.training_set.close()
                except:
                    self.iteration = 0

    def run_neb(self, images, interpolate=False, fmax=None):
        """This method runs NEB calculations

        Parameters
        ----------
        images : atom objects
            Images created with ASE.
        interpolate : bool
            Interpolate images. Needed when initializing this module.
        fmax : the maximum force to be used in your NEB.
        """
        neb = NEB(images)

        if interpolate is True:
            neb.interpolate()
            calc = self.calc
            self.set_calculators(neb.images,
                                 calc,
                                 calc_name=self.calc_name,
                                 write_training_set=True,
                                 cores=self.cores)
            del calc
            return neb.images

        else:
            self.traj = 'neb_%s.traj' % self.iteration
            logfile = 'neb_%s.log' % self.iteration

            if self.neb_optimizer.lower() == 'bfgs':
                from ase.optimize import BFGS
                qn = BFGS(neb, trajectory=self.traj, logfile=logfile)
            elif self.neb_optimizer.lower() == 'fire':
                from ase.optimize import FIRE
                qn = FIRE(neb, trajectory=self.traj, logfile=logfile)

            if self.maxrunsteps is None:
                qn.run(fmax=fmax)
            else:
                qn.run(fmax=fmax, steps=self.maxrunsteps)
        clean_dir(logfile=self.logfile)

    def accelerate(self):
        """This method performs all the acceleration algorithm"""

        nreadimg = -(self.intermediates + 2)

        if self.step is None:
            step = 1.
        else:
            step = self.step
        fmax = self.ifmax

        if self.initialized is True and self.trained is False:
            self.iteration = 0
            self.training_set = Trajectory('training.traj')
            self.logfile.write('Iteration %s \n' % self.iteration)
            self.logfile.write('Number images to slice from NEB Trajectory is '
                               '%s. \n' % nreadimg)
            self.logfile.write('New training set lenght is %s. \n' %
                               len(self.training_set))
            self.logfile.flush()

            self.logfile.write('Starting Training, sit tight... \n')
            self.logfile.flush()
            label = str(self.iteration)
            amp_calc = copy.deepcopy(self.amp_calc)
            amp_calc.set_label(label)
            self.train(self.training_set, amp_calc, label=label)
            del amp_calc
            clean_train_data()
            self.logfile.write('Training process finished. \n')
            self.logfile.flush()

            self.logfile.write('Step = %s, ifmax = %s, fmax = %s \n' %
                               (step, fmax, self.fmax))
            self.logfile.write('Starting ML-NEB calculation... '
                               'Go, and grab a cup of coffee :) \n')
            self.logfile.flush()
            newcalc = Amp.load('%s.amp' % label)
            calc_name = newcalc.__class__.__name__

            self.neb_images = read('training.traj',
                                   index=slice(0, self.nreadimg))

            images = self.set_calculators(
                    self.neb_images,
                    newcalc,
                    calc_name=calc_name,
                    logfile=self.logfile,
                    cores=self.cores
                    )

            self.run_neb(self.neb_images, fmax=fmax)
            del newcalc
            clean_dir(logfile=self.logfile)
            self.logfile.write('ML-NEB calculation finished... \o/ \n')

            # We now read the last images from the NEB: initial, intermediate,
            # and final states.
            ini_neb_images = read(self.traj, index=slice(nreadimg, None))
            self.logfile.write('New guessed ML-MEP was read from %s \n'
                               % self.traj)
            self.logfile.flush()

            newcalc = Amp.load('%s.amp' % label)
            self.achieved = self.cross_validate(ini_neb_images,
                                                calc=self.calc,
                                                amp_calc=newcalc,
                                                metric=self.metric)
            clean_dir(logfile=self.logfile)
            del newcalc
            self.logfile.flush()
            self.training_set.close()

        while True:
            self.iteration += 1

            self.logfile.write('Iteration %s \n' % self.iteration)
            self.logfile.flush()

            fmax = fmax / step
            if fmax < self.fmax or fmax == self.fmax:
                fmax = self.fmax
            else:
                self.logfile.write('Step = %s, new ifmax = %s \n'
                                   % (step, fmax))
                self.logfile.flush()

            if ((self.achieved[0] > self.tolerance) or
               (self.achieved[1] > self.tolerance)):
                print('Line 182', fmax)
                if (self.iteration - 1) == 0:
                    self.logfile.write('INITIAL\n')
                    self.logfile.flush()
                    if os.path.isfile('images_from_neb.traj'):
                        ini_neb_images = Trajectory('images_from_neb.traj',
                                                    mode='r')
                        ini_neb_images = list(ini_neb_images)[1:-1]
                        training_file = Trajectory('training.traj',
                                                   mode='a')
                        for _ in ini_neb_images:
                            training_file.write(_)
                        training_file.close()
                    else:
                        self.logfile.write('images_from_neb.traj does not '
                                           'exist\n')
                        self.logfile.write('Aborting...\n')
                        exit()
                    self.logfile.write('I added %s more images to the training'
                                       'set \n'
                                       % len(ini_neb_images))
                    self.logfile.flush()
                else:
                    self.traj_to_add = 'neb_%s.traj' % (self.iteration - 1)
                    self.logfile.write('Previous NEB Trajectory read from %s'
                                       '\n' % self.traj_to_add)
                    self.logfile.flush()
                    if os.path.isfile('images_from_neb.traj'):
                        ini_neb_images = Trajectory('images_from_neb.traj',
                                                    mode='r')
                        ini_neb_images = list(ini_neb_images)[1:-1]
                        training_file = Trajectory('training.traj', mode='a')
                        for _ in ini_neb_images:
                            training_file.write(_)
                        training_file.close()
                    else:
                        self.logfile.write('images_from_neb.traj does not'
                                           'exist\n')
                        self.logfile.write('Aborting...\n')
                        exit()
                    self.logfile.write('I added %s more images to the training'
                                       'set\n' % len(ini_neb_images))
                    self.logfile.flush()

                self.training_set = Trajectory('training.traj')
                self.logfile.write('Length of training set is now %s.\n'
                                   % len(self.training_set))
                label = str(self.iteration)
                amp_calc = copy.deepcopy(self.amp_calc)
                amp_calc.set_label(label)
                self.train(self.training_set, amp_calc, label=label)
                del amp_calc
                clean_train_data()
                newcalc = Amp.load('%s.amp' % label)
                calc_name = newcalc.__class__.__name__

                if self.previous_nebfile is False:
                    self.neb_images = read('training.traj',
                                           index=slice(0, self.nreadimg))
                else:
                    nebfile = 'neb_%s.traj' % (self.iteration - 1)
                    self.neb_images = read(nebfile,
                                           index=slice(-self.nreadimg, None))

                images = self.set_calculators(self.neb_images, newcalc,
                                              calc_name=calc_name,
                                              logfile=self.logfile,
                                              cores=self.cores)

                self.run_neb(images, fmax=fmax)
                clean_dir(logfile=self.logfile)
                del newcalc
                new_neb_images = read(self.traj, index=slice(nreadimg, None))
                newcalc = Amp.load('%s.amp' % label)
                self.achieved = self.cross_validate(
                        new_neb_images,
                        calc=self.calc,
                        amp_calc=newcalc,
                        metric=self.metric
                        )
                clean_dir(logfile=self.logfile)
                del newcalc
                self.logfile.flush()

                if ((self.achieved[0] < self.tolerance) and
                   (self.achieved[1] < self.tolerance) and
                   (fmax <= self.fmax)):
                    self.final_fmax = True

            elif self.iteration == self.maxiter:
                print('Line 294', fmax)
                self.logfile.write('Maximum number of iterations reached')
                break

            elif fmax == self.fmax and self.final_fmax is True:
                print('Line 299', fmax)
                self.logfile.write('\n')
                self.logfile.write("Calculation converged!\n")
                self.logfile.write('     fmax = %s.\n' % fmax)
                self.logfile.write('tolerance = %s.\n' % float(self.tolerance))
                self.logfile.write(' Energy error= %s.\n'
                                   % float(self.achieved[0]))
                self.logfile.write(' Forces error= %s.\n'
                                   % float(self.achieved[1]))
                break

            elif fmax < self.fmax:
                self.logfile.write('Iteration %s \n' % self.iteration)
                self.logfile.flush()
                print('Line 248', fmax)
                self.traj_to_add = 'neb_%s.traj' % (self.iteration - 1)
                self.logfile.write('Trajectory to be added %s \n'
                                   % self.traj_to_add)
                self.logfile.flush()

                if os.path.isfile('images_from_neb.traj'):
                    ini_neb_images = Trajectory('images_from_neb.traj',
                                                mode='r')
                    ini_neb_images = list(ini_neb_images)[1:-1]
                    training_file = Trajectory('training.traj', mode='a')
                    for _ in ini_neb_images:
                        training_file.write(_)
                    training_file.close()
                else:
                    self.logfile.write('images_from_neb.traj does not exist\n')
                    self.logfile.write('Aborting...\n')
                    exit()
                self.logfile.write('I added %s more images to the training set'
                                   '\n' % len(ini_neb_images))
                self.logfile.flush()

                self.training_set = Trajectory('training.traj')
                self.logfile.write('Length of training set is now %s.\n'
                                   % len(self.training_set))

                label = str(self.iteration)
                amp_calc = copy.deepcopy(self.amp_calc)
                amp_calc.set_label(label)
                self.train(self.training_set, amp_calc, label=label)
                del amp_calc
                clean_train_data()
                newcalc = Amp.load('%s.amp' % label)

                if self.previous_nebfile is False:
                    self.neb_images = read('training.traj', index=slice(0,
                                           self.nreadimg))
                else:
                    nebfile = 'neb_%s.traj' % (self.iteration - 1)
                    self.neb_images = read(nebfile,
                                           index=slice(-self.nreadimg, None))

                images = self.set_calculators(self.neb_images, newcalc,
                                              calc_write=self.calc_write,
                                              logfile=self.logfile,
                                              cores=self.cores)

                fmax = self.fmax
                self.logfile.write('Step = %s, input requested fmax = %s \n'
                                   % (step, fmax))
                self.run_neb(images, fmax=fmax)
                clean_dir(logfile=self.logfile)
                del newcalc
                new_neb_images = read(self.traj, index=slice(nreadimg, None))
                newcalc = Amp.load('%s.amp' % label)
                self.achieved = self.cross_validate(new_neb_images,
                                                    calc=self.calc,
                                                    amp_calc=newcalc,
                                                    metric=self.metric)
                self.logfile.write('Energy and Force metrics achieved are %s '
                                   'and %s, tolerance requested is %s \n'
                                   % (float(self.achieved[0]),
                                      float(self.achieved[1]),
                                      self.tolerance))
                clean_dir(logfile=self.logfile)
                del newcalc
                self.logfile.flush()

                if ((self.achieved[0] < self.tolerance) and
                   (self.achieved[1] < self.tolerance) and
                   (fmax <= self.fmax)):
                    self.final_fmax = True

            else:
                print('Line 307', fmax)
                self.logfile.write('Iteration %s \n' % self.iteration)
                self.logfile.flush()
                self.traj_to_add = 'neb_%s.traj' % (self.iteration - 1)
                self.logfile.write('Trajectory to be added %s \n'
                                   % self.traj_to_add)
                self.logfile.flush()

                if os.path.isfile('images_from_neb.traj'):
                    ini_neb_images = Trajectory('images_from_neb.traj',
                                                mode='r')
                    ini_neb_images = list(ini_neb_images)[1:-1]
                    training_file = Trajectory('training.traj', mode='a')
                    for _ in ini_neb_images:
                        training_file.write(_)
                    training_file.close()

                else:
                    self.logfile.write('images_from_neb.traj does not exist\n')
                    self.logfile.write('Aborting...\n')
                    exit()
                self.logfile.write('I added %s more images to the training set'
                                   '\n' % len(ini_neb_images))
                self.logfile.flush()

                self.training_set = Trajectory('training.traj')
                self.logfile.write('Length of training set is now %s.\n'
                                   % len(self.training_set))
                label = str(self.iteration)
                amp_calc = copy.deepcopy(self.amp_calc)
                amp_calc.set_label(label)
                self.train(self.training_set, amp_calc, label=label)
                del amp_calc
                clean_train_data()
                newcalc = Amp.load('%s.amp' % label)
                calc_name = newcalc.__class__.__name__

                if self.previous_nebfile is False:
                    self.neb_images = read('training.traj',
                                           index=slice(0, self.nreadimg))
                else:
                    nebfile = 'neb_%s.traj' % (self.iteration - 1)
                    self.neb_images = read(nebfile,
                                           index=slice(-self.nreadimg, None))

                images = self.set_calculators(self.neb_images, newcalc,
                                              calc_name=calc_name,
                                              logfile=self.logfile,
                                              cores=self.cores)

                self.run_neb(images, fmax=fmax)
                clean_dir(logfile=self.logfile)
                del newcalc
                new_neb_images = read(self.traj, index=slice(nreadimg, None))
                newcalc = Amp.load('%s.amp' % label)
                self.achieved = self.cross_validate(new_neb_images,
                                                    calc=self.calc,
                                                    amp_calc=newcalc,
                                                    metric=self.metric)
                clean_dir(logfile=self.logfile)
                del newcalc
                self.logfile.flush()

                if ((self.achieved[0] < self.tolerance) and
                   (self.achieved[1] < self.tolerance) and
                   (fmax <= self.fmax)):
                    self.final_fmax = True

    def train(self, trainingset, amp_calc, label=None):
        """This method takes care of training

        Parameters
        ----------
        trainingset : object
            List of images to be trained.
        amp_calc : object
            The Amp instance to do the training of the model.
        label : str
            An integer converted to string.
        """
        if label is None:
            label = str(self.iteration)
        try:
            calc = copy.deepcopy(amp_calc)
            calc.dblabel = label
            calc.label = label
            calc.train(trainingset)
            # subprocess.call(['mv', 'amp-log.txt', label + '-train.log'])
            del calc
        except:
            raise

    def cross_validate(self, neb_images, calc=None, amp_calc=None,
                       metric='fmax'):
        """Cross validate

        This method will verify whether or not a metric to measure error
        between predictions and targets meets the desired criterium. It uses
        metrics found in scikit-learn module.

        Parameters
        ----------
        neb_images : objects
            These are the images read after a successful NEB calculation.
        calc : object
            This is the calculator used to perform DFT calculations.
        amp_calc : object
            This is the machine learning model used to perform predictions.
        """
        self.logfile.write('Length of NEB images %s \n' % len(neb_images))

        # Computing energies and forces using Amp.

        calc_name = amp_calc.__class__.__name__

        amp_energies = []
        amp_forces = []

        amp_images = self.set_calculators(neb_images, amp_calc,
                                          calc_name=calc_name)

        for image in amp_images:
            energy = image.get_potential_energy()
            forces = image.get_forces()
            amp_energies.append(energy)
            amp_forces.append(forces.sum(axis=1))

        # Computing energies and forces from references
        dft_energies = []
        dft_forces = []

        self.set_calculators(neb_images, amp_calc, calc_name=self.calc_name,
                             cores=self.cores, cross_validate=True)

        dft_images = []
        dft_images.append(self.training_set[0])

        dft_intermediates = Trajectory('calculator.traj')

        for intermediate in dft_intermediates:
            dft_images.append(intermediate)

        dft_images.append(self.training_set[len(dft_images)])

        images_from_neb = Trajectory('images_from_neb.traj', mode='w')

        for i in range(len(dft_images)):
            energy = dft_images[i].get_potential_energy()
            forces = dft_images[i].get_forces()
            dft_energies.append(energy)
            dft_forces.append(forces.sum(axis=1))

            images_from_neb.write(dft_images[i])

        images_from_neb.close()

        if metric == 'fmax':
            f_metric = get_fmax(dft_images)
            e_metric = f_metric
            self.logfile.write('fmax achieved is %s, tolerance requested is '
                               '%s\n' % (float(f_metric), self.tolerance))
            return e_metric, f_metric

        elif metric.lower() == 'mae':
            e_metric = mean_absolute_error(dft_energies, amp_energies)
            f_metric = mean_absolute_error(dft_forces, amp_forces)

            self.logfile.write('Energy and Force MAE achieved are %s '
                               'and %s, tolerance requested is %s\n'
                               % (float(e_metric),
                                  float(f_metric),
                                  self.tolerance))
            return e_metric, f_metric

    def run_gpaw(self, images):
        """Method for running gpaw weird parallelization

        Parameters
        ---------
        images : object
            The images.
        """
        input_traj = Trajectory('input.traj', mode='w')

        for image in images:
            input_traj.write(image)
        input_traj.close()

        cores = str(self.cores)
        gpaw = [
                'mpiexec',
                '-n', cores,
                'gpaw-python',
                'gpaw_script.py'
                ]
        subprocess.call(gpaw)

    def set_calculators(self, images, calc, calc_name=None, label=None,
                        logfile=None, write_training_set=False, cores=None,
                        cross_validate=False):
        """Function to set calculators

        Parameters
        ----------
        images : object
            The images to set calculators.
        calc : object
            Calculator.
        calc_name : str
            Name of the calculator. Useful when running GPAW.
        label : str
            Set a label for Amp calculators.
        logfile : str
            Path to create logfile.
        write_training_set : bool
            Whether we will write (or not) training set to a trajectory file.
        cross_validate : bool
            Whether this method is called or not for cross validating or not.
        """

        if label is not None:
            self.logfile.write('Label was set to %s\n' % label)
            calc.label = label

        if write_training_set is True:
            if os.path.isfile('training.traj'):
                training_file = Trajectory('training.traj', mode='a')
            else:
                training_file = Trajectory('training.traj', mode='w')

        if calc_name != 'GPAW':
            intermediate_traj = Trajectory('calculator.traj', mode='w')
            if cross_validate is True:
                images = images[1:-1]

            for index in range(len(images)):
                images[index].set_calculator(calc)
                images[index].get_potential_energy(apply_constraint=False)
                images[index].get_forces(apply_constraint=False)
                intermediate_traj.write(images[index])
        else:
            write_gpaw_file()
            if cross_validate is True:
                # We only need the energy and forces of intermediates!
                intermediates = images[1:-1]
                self.run_gpaw(intermediates)
            else:
                self.run_gpaw(images)

        if write_training_set is True:
            to_dump = Trajectory('calculator.traj', mode='r')
            for element in to_dump:
                training_file.write(element)

        if logfile is not None:
            logfile.write('Calculator set for %s images\n' % len(images))
            logfile.flush()

        if write_training_set is True and calc_name != 'GPAW':
            training_file.close()
        return images


def get_fmax(images, **kwargs):
    """Returns fmax, as used by optimizers with NEB."""
    neb = NEB(images, **kwargs)
    forces = neb.get_forces()
    return np.sqrt((forces**2).sum(axis=1).max())


def clean_dir(logfile=None):
    """Cleaning some directories"""
    remove = [
            'rm',
            '-rf',
            'amp-fingerprint-primes.ampdb',
            'amp-fingerprints.ampdb',
            'amp-log.txt',
            'amp-neighborlists.ampdb'
            ]
    subprocess.call(remove)

    if logfile is not None:
        logfile.write('Cleaning up...\n')
        logfile.flush()


def clean_train_data():
    subprocess.call('rm -r *.ampdb', shell=True)


def write_gpaw_file():
    """Ugly function that writes a gpaw python script. The problem is not you
    `write_gpaw_file()`, the problem is me.
    """

    gpaw_file = open('gpaw_script.py', 'w')
    header0 = """#!/usr/bin/env python
from gpaw import GPAW, PW, FermiDirac
from ase.io import read, Trajectory, write

input = Trajectory('input.traj', mode='r')
output = Trajectory('calculator.traj', mode='w')

"""
    gpaw_file.write(header0)

    reading = open('gpaw.calc', 'r')
    header1 = reading.readlines()
    for line in header1:
        gpaw_file.write(line)

    header2 = """
for image in input:
    image.set_calculator(calc)
    image.get_potential_energy()
    image.get_forces()
    output.write(image)

output.close()
"""
    gpaw_file.write(header2)


def check_files():
    """ Check files
    """
    import glob
    import os

    logfiles = sorted(glob.glob('*.txt'))

    if 'amp-log.txt' in logfiles:
        logfiles.remove('amp-log.txt')

    if 'out.txt' in logfiles:
        logfiles.remove('out.txt')

    if len(logfiles) is not 0:
        digit = logfiles[-1][0]

        nebfile = 'neb_%s.traj' % digit

        if os.path.isfile(nebfile):
            return nebfile, digit
        else:
            return False, digit
    else:
        return False, None
