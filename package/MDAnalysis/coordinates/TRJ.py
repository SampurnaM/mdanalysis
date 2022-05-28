# -*- Mode: python; tab-width: 4; indent-tabs-mode:nil; coding:utf-8 -*-
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
#
# MDAnalysis --- https://www.mdanalysis.org
# Copyright (c) 2006-2017 The MDAnalysis Development Team and contributors
# (see the file AUTHORS for the full list of names)
#
# Released under the GNU Public Licence, v2 or any higher version
#
# Please cite your use of MDAnalysis in published work:
#
# R. J. Gowers, M. Linke, J. Barnoud, T. J. E. Reddy, M. N. Melo, S. L. Seyler,
# D. L. Dotson, J. Domanski, S. Buchoux, I. M. Kenney, and O. Beckstein.
# MDAnalysis: A Python package for the rapid analysis of molecular dynamics
# simulations. In S. Benthall and S. Rostrup editors, Proceedings of the 15th
# Python in Science Conference, pages 102-109, Austin, TX, 2016. SciPy.
# doi: 10.25080/majora-629e541a-00e
#
# N. Michaud-Agrawal, E. J. Denning, T. B. Woolf, and O. Beckstein.
# MDAnalysis: A Toolkit for the Analysis of Molecular Dynamics Simulations.
# J. Comput. Chem. 32 (2011), 2319--2327, doi:10.1002/jcc.21787
#
"""AMBER trajectories --- :mod:`MDAnalysis.coordinates.TRJ`
========================================================

AMBER_ can write :ref:`ASCII trajectories<ascii-trajectories>` ("traj") and
:ref:`binary trajectories<netcdf-trajectories>` ("netcdf"). MDAnalysis supports
reading of both formats and writing for the binary trajectories.

Note
----
Support for AMBER is still somewhat *experimental* and feedback and
contributions are highly appreciated. Use the `Issue Tracker`_ or get in touch
on the `MDAnalysis mailinglist`_.


.. rubric:: Units

AMBER trajectories are assumed to be in the following units:

* lengths in Angstrom (Å)
* time in ps (but see below)


.. _netcdf-trajectories:

Binary NetCDF trajectories
--------------------------

The `AMBER netcdf`_ format make use of NetCDF_ (Network Common Data
Form) format. Such binary trajectories are recognized in MDAnalysis by
the '.ncdf' suffix and read by the :class:`NCDFReader`.

Binary trajectories can also contain velocities and forces, and can record the
exact time
step. In principle, the trajectories can be in different units than the AMBER
defaults of ångström and picoseconds but at the moment MDAnalysis only supports
those and will raise a :exc:`NotImplementedError` if anything else is detected.

.. autoclass:: NCDFReader
   :members:

.. autoclass:: NCDFWriter
   :members:

.. autoclass:: NCDFPicklable
   :members:

.. _ascii-trajectories:

ASCII TRAJ trajectories
-----------------------

ASCII AMBER_ TRJ coordinate files (as defined in `AMBER TRJ format`_)
are handled by the :class:`TRJReader`. It is also possible to directly
read *bzip2* or *gzip* compressed files.

AMBER ASCII trajectories are recognised by the suffix '.trj',
'.mdcrd' or '.crdbox (possibly with an additional '.gz' or '.bz2').

.. Note::

   In the AMBER community, these trajectories are often saved with the
   suffix '.crd' but this extension conflicts with the CHARMM CRD
   format and MDAnalysis *will not correctly autodetect AMBER ".crd"
   trajectories*. Instead, explicitly provide the ``format="TRJ"``
   argument to :class:`~MDAnalysis.core.universe.Universe`::

     u = MDAnalysis.Universe("top.prmtop", "traj.crd", format="TRJ")

   In this way, the AMBER :class:`TRJReader` is used.


.. rubric:: Limitations

* Periodic boxes are only stored as box lengths A, B, C in an AMBER
  trajectory; the reader always assumes that these are orthorhombic
  boxes.

* The trajectory does not contain time information so we simply set
  the time step to 1 ps (or the user could provide it as kwarg *dt*)

* Trajectories with fewer than 4 atoms probably fail to be read (BUG).

* If the trajectory contains exactly *one* atom then it is always
  assumed to be non-periodic (for technical reasons).

* Velocities are currently *not supported* as ASCII trajectories.

.. autoclass:: TRJReader
   :members:



.. Links

.. _AMBER: http://ambermd.org
.. _AMBER TRJ format: http://ambermd.org/formats.html#trajectory
..    The formats page was archived as
..    http://www.webcitation.org/query?url=http%3A%2F%2Fambermd.org%2Fformats.html&date=2018-02-11
..    Use the archived version if the original disappears. [orbeckst]
.. _AMBER netcdf format: http://ambermd.org/netcdf/nctraj.xhtml
..    The formats page was archived as
..    http://www.webcitation.org/query?url=http%3A%2F%2Fambermd.org%2Fnetcdf%2Fnctraj.xhtml&date=2018-02-11
..    Use the archived version if the original disappears. [orbeckst]
.. _AMBER netcdf: http://ambermd.org/netcdf/nctraj.xhtml
.. _NetCDF: http://www.unidata.ucar.edu/software/netcdf
.. _Issue Tracker: https://github.com/MDAnalysis/mdanalysis/issues
.. _MDAnalysis mailinglist: https://groups.google.com/group/mdnalysis-discussion

"""
import scipy.io.netcdf
import numpy as np
import warnings
import errno
import logging
from math import isclose

import MDAnalysis
from .timestep import Timestep
from . import base
from ..lib import util
logger = logging.getLogger("MDAnalysis.coordinates.AMBER")


try:
    import netCDF4
except ImportError:
    netCDF4 = None
    logger.warning("netCDF4 is not available. Writing AMBER ncdf files will be slow.")


class TRJReader(base.ReaderBase):
    """AMBER trajectory reader.

    Reads the ASCII formatted `AMBER TRJ format`_. Periodic box information
    is auto-detected.

    The number of atoms in a timestep *must* be provided in the `n_atoms`
    keyword because it is not stored in the trajectory header and cannot be
    reliably autodetected. The constructor raises a :exc:`ValueError` if
    `n_atoms` is left at its default value of ``None``.

    The length of a timestep is not stored in the trajectory itself but can
    be set by passing the `dt` keyword argument to the constructor; it
    is assumed to be in ps. The default value is 1 ps.

    .. _AMBER TRJ format: http://ambermd.org/formats.html#trajectory

    .. versionchanged:: 0.11.0
       Frames now 0-based instead of 1-based.
       kwarg `delta` renamed to `dt`, for uniformity with other Readers
    """
    format = ['TRJ', 'MDCRD', 'CRDBOX']
    units = {'time': 'ps', 'length': 'Angstrom'}
    _Timestep = Timestep

    def __init__(self, filename, n_atoms=None, **kwargs):
        super(TRJReader, self).__init__(filename, **kwargs)
        if n_atoms is None:
            raise ValueError("AMBER TRJ reader REQUIRES the n_atoms keyword")
        self._n_atoms = n_atoms
        self._n_frames = None

        self.trjfile = None  # have _read_next_timestep() open it properly!
        self.ts = self._Timestep(self.n_atoms, **self._ts_kwargs)

        # FORMAT(10F8.3)  (X(i), Y(i), Z(i), i=1,NATOM)
        self.default_line_parser = util.FORTRANReader("10F8.3")
        self.lines_per_frame = int(np.ceil(3.0 * self.n_atoms / len(
            self.default_line_parser)))
        # The last line per frame might have fewer than 10
        # We determine right away what parser we need for the last
        # line because it will be the same for all frames.
        last_per_line = 3 * self.n_atoms % len(self.default_line_parser)
        self.last_line_parser = util.FORTRANReader("{0:d}F8.3".format(
            last_per_line))

        # FORMAT(10F8.3)  BOX(1), BOX(2), BOX(3)
        # is this always on a separate line??
        self.box_line_parser = util.FORTRANReader("3F8.3")

        # Now check for box
        self._detect_amber_box()

        # open file, read first frame
        self._read_next_timestep()

    def _read_frame(self, frame):
        if self.trjfile is None:
            self.open_trajectory()
        self.trjfile.seek(self._offsets[frame])
        self.ts.frame = frame - 1  # gets +1'd in _read_next
        return self._read_next_timestep()

    def _read_next_timestep(self):
        # FORMAT(10F8.3)  (X(i), Y(i), Z(i), i=1,NATOM)
        ts = self.ts
        if self.trjfile is None:
            self.open_trajectory()

        # Read coordinat frame:
        # coordinates = numpy.zeros(3*self.n_atoms, dtype=np.float32)
        _coords = []
        for number, line in enumerate(self.trjfile):
            try:
                _coords.extend(self.default_line_parser.read(line))
            except ValueError:
                # less than 10 entries on the line:
                _coords.extend(self.last_line_parser.read(line))
            if number == self.lines_per_frame - 1:
                # read all atoms that are there in this frame
                break
        if _coords == []:
            # at the end of the stream (the loop has not been entered)
            raise EOFError

        # Read box information
        if self.periodic:
            line = next(self.trjfile)
            box = self.box_line_parser.read(line)
            ts.dimensions = box + [90., 90., 90.]  # assumed

        # probably slow ... could be optimized by storing the coordinates in
        # X,Y,Z lists or directly filling the array; the array/reshape is not
        # good because it creates an intermediate array
        ts._pos[:] = np.array(_coords).reshape(self.n_atoms, 3)
        ts.frame += 1
        return ts

    def _detect_amber_box(self):
        """Detecting a box in a AMBER trajectory

        Rewind trajectory and check for potential box data
        after the first frame.

        Set :attr:`TRJReader.periodic` to ``True`` if box was
        found, ``False`` otherwise.

        Only run at the beginning as it *rewinds* the trajctory.

         - see if there's data after the atoms have been read that looks
           like::

             FORMAT(10F8.3)  BOX(1), BOX(2), BOX(3)
             BOX    : size of periodic box

         - this WILL fail if we have exactly 1 atom in the trajectory because
           there's no way to distinguish the coordinates from the box
           so for 1 atom we always assume no box

        .. TODO:: needs a Timestep that knows about AMBER unitcells!
        """
        if self.n_atoms == 1:
            # for 1 atom we cannot detect the box with the current approach
            self.periodic = False  # see _read_next_timestep()!
            wmsg = "Trajectory contains a single atom: assuming periodic=False"
            warnings.warn(wmsg)
            return False

        self._reopen()
        self.periodic = False  # make sure that only coordinates are read
        self._read_next_timestep()
        ts = self.ts
        # TODO: what do we do with 1-frame trajectories? Try..except EOFError?
        line = next(self.trjfile)
        nentries = self.default_line_parser.number_of_matches(line)
        if nentries == 3:
            self.periodic = True
            ts.dimensions = self.box_line_parser.read(line) + [90., 90., 90.]
        else:
            self.periodic = False
        self.close()
        return self.periodic

    @property
    def n_frames(self):
        """Number of frames (obtained from reading the whole trajectory)."""
        if self._n_frames is not None:  # return cached value
            return self._n_frames
        try:
            self._n_frames = self._read_trj_n_frames(self.filename)
        except IOError:
            return 0
        else:
            return self._n_frames

    def _read_trj_n_frames(self, filename):
        lpf = self.lines_per_frame
        if self.periodic:
            lpf += 1

        self._offsets = offsets = []
        counter = 0
        with util.openany(self.filename) as f:
            line = f.readline()  # ignore first line
            while line:
                if counter % lpf == 0:
                    offsets.append(f.tell())
                line = f.readline()
                counter += 1
        offsets.pop()  # last offset is EOF
        return len(offsets)

    @property
    def n_atoms(self):
        return self._n_atoms

    def _reopen(self):
        self.close()
        self.open_trajectory()

    def open_trajectory(self):
        """Open the trajectory for reading and load first frame."""
        self.trjfile = util.anyopen(self.filename)
        self.header = self.trjfile.readline()  # ignore first line
        if len(self.header.rstrip()) > 80:
            # Chimera uses this check
            raise OSError(
                "Header of AMBER formatted trajectory has more than 80 chars. "
                "This is probably not a AMBER trajectory.")
        # reset ts
        ts = self.ts
        ts.frame = -1

        return self.trjfile

    def close(self):
        """Close trj trajectory file if it was open."""
        if self.trjfile is None:
            return
        self.trjfile.close()
        self.trjfile = None


class NCDFReader(base.ReaderBase):
    """Reader for `AMBER NETCDF format`_ (version 1.0).

    AMBER binary trajectories are automatically recognised by the
    file extension ".ncdf".

    The number of atoms (`n_atoms`) does not have to be provided as it can
    be read from the trajectory. The trajectory reader can randomly access
    frames and therefore supports direct indexing (with 0-based frame
    indices) and full-feature trajectory iteration, including slicing.

    Velocities are autodetected and read into the
    :attr:`Timestep._velocities` attribute.

    Forces are autodetected and read into the
    :attr:`Timestep._forces` attribute.

    Periodic unit cell information is detected and used to populate the
    :attr:`Timestep.dimensions` attribute. (If no unit cell is available in
    the trajectory, then :attr:`Timestep.dimensions` will return
    ``[0,0,0,0,0,0]``).

    Current limitations:

    * only trajectories with time in ps and lengths in Angstroem are processed

    The NCDF reader uses :mod:`scipy.io.netcdf` and therefore :mod:`scipy` must
    be installed. It supports the *mmap* keyword argument (when reading):
    ``mmap=True`` is memory efficient and directly maps the trajectory on disk
    to memory (using the :class:`~mmap.mmap`); ``mmap=False`` may consume large
    amounts of memory because it loads the whole trajectory into memory but it
    might be faster. The default is ``mmap=None`` and then default behavior of
    :class:`scipy.io.netcdf_file` prevails, i.e. ``True`` when
    *filename* is a file name, ``False`` when *filename* is a file-like object.

    .. _AMBER NETCDF format: http://ambermd.org/netcdf/nctraj.xhtml

    See Also
    --------
    :class:`NCDFWriter`


    .. versionadded: 0.7.6
    .. versionchanged:: 0.10.0
       Added ability to read Forces
    .. versionchanged:: 0.11.0
       Frame labels now 0-based instead of 1-based.
       kwarg `delta` renamed to `dt`, for uniformity with other Readers.
    .. versionchanged:: 0.17.0
       Uses :mod:`scipy.io.netcdf` and supports the *mmap* kwarg.
    .. versionchanged:: 0.20.0
       Now reads scale_factors for all expected AMBER convention variables.
       Timestep variables now adhere standard MDAnalysis units, with lengths
       of angstrom, time of ps, velocity of angstrom/ps and force of
       kJ/(mol*Angstrom). It is noted that with 0.19.2 and earlier versions,
       velocities would have often been reported in values of angstrom/AKMA
       time units instead (Issue #2323).
    .. versionchanged:: 1.0.0
       Support for reading `degrees` units for `cell_angles` has now been
       removed (Issue #2327)
    .. versionchanged:: 2.0.0
       Now use a picklable :class:`scipy.io.netcdf_file`--
       :class:`NCDFPicklable`.
       Reading of `dt` now defaults to 1.0 ps if `dt` cannot be extracted from
       the first two frames of the trajectory.
       :meth:`Writer` now also sets `convert_units`, `velocities`, `forces` and
       `scale_factor` information for the :class:`NCDFWriter`.

    """

    format = ['NCDF', 'NC']
    multiframe = True
    version = "1.0"
    units = {'time': 'ps',
             'length': 'Angstrom',
             'velocity': 'Angstrom/ps',
             'force': 'kcal/(mol*Angstrom)'}

    _Timestep = Timestep

    def __init__(self, filename, n_atoms=None, mmap=None, **kwargs):

        self._mmap = mmap

        super(NCDFReader, self).__init__(filename, **kwargs)

        # ensure maskandscale is off so we don't end up double scaling
        self.trjfile = NCDFPicklable(self.filename,
                                     mmap=self._mmap,
                                     maskandscale=False)

        # AMBER NetCDF files should always have a convention
        try:
            conventions = self.trjfile.Conventions
            if not ('AMBER' in conventions.decode('utf-8').split(',') or
                    'AMBER' in conventions.decode('utf-8').split()):
                errmsg = ("NCDF trajectory {0} does not conform to AMBER "
                          "specifications, "
                          "http://ambermd.org/netcdf/nctraj.xhtml "
                          "('AMBER' must be one of the token in attribute "
                          "Conventions)".format(self.filename))
                logger.fatal(errmsg)
                raise TypeError(errmsg)
        except AttributeError:
            errmsg = "NCDF trajectory {0} is missing Conventions".format(
                      self.filename)
            logger.fatal(errmsg)
            raise ValueError(errmsg) from None

        # AMBER NetCDF files should also have a ConventionVersion
        try:
            ConventionVersion = self.trjfile.ConventionVersion.decode('utf-8')
            if not ConventionVersion == self.version:
                wmsg = ("NCDF trajectory format is {0!s} but the reader "
                        "implements format {1!s}".format(
                         ConventionVersion, self.version))
                warnings.warn(wmsg)
                logger.warning(wmsg)
        except AttributeError:
            errmsg = "NCDF trajectory {0} is missing ConventionVersion".format(
                      self.filename)
            raise ValueError(errmsg) from None

        # The AMBER NetCDF standard enforces 64 bit offsets
        if not self.trjfile.version_byte == 2:
            errmsg = ("NCDF trajectory {0} does not conform to AMBER "
                      "specifications, as detailed in "
                      "https://ambermd.org/netcdf/nctraj.xhtml "
                      "(NetCDF file does not use 64 bit offsets "
                      "[version_byte = 2])".format(self.filename))
            logger.fatal(errmsg)
            raise TypeError(errmsg)

        # The AMBER NetCDF standard enforces 3D coordinates
        try:
            if not self.trjfile.dimensions['spatial'] == 3:
                errmsg = "Incorrect spatial value for NCDF trajectory file"
                raise TypeError(errmsg)
        except KeyError:
            errmsg = "NCDF trajectory does not contain spatial dimension"
            raise ValueError(errmsg) from None

        # AMBER NetCDF specs require program and programVersion. Warn users
        # if those attributes do not exist
        if not (hasattr(self.trjfile, 'program') and
                hasattr(self.trjfile, 'programVersion')):
            wmsg = ("NCDF trajectory {0} may not fully adhere to AMBER "
                    "standards as either the `program` or `programVersion` "
                    "attributes are missing".format(self.filename))
            warnings.warn(wmsg)
            logger.warning(wmsg)

        try:
            self.n_atoms = self.trjfile.dimensions['atom']
            if n_atoms is not None and n_atoms != self.n_atoms:
                errmsg = ("Supplied n_atoms ({0}) != natom from ncdf ({1}). "
                          "Note: n_atoms can be None and then the ncdf value "
                          "is used!".format(n_atoms, self.n_atoms))
                raise ValueError(errmsg)
        except KeyError:
            errmsg = ("NCDF trajectory {0} does not contain atom "
                      "information".format(self.filename))
            raise ValueError(errmsg) from None

        try:
            self.n_frames = self.trjfile.dimensions['frame']
            # example trajectory when read with scipy.io.netcdf has
            # dimensions['frame'] == None (indicating a record dimension that
            # can grow) whereas if read with netCDF4 I get
            # len(dimensions['frame']) ==  10: in any case, we need to get
            # the number of frames from somewhere such as the time variable:
            if self.n_frames is None:
                self.n_frames = self.trjfile.variables['time'].shape[0]
        except KeyError:
            errmsg = (f"NCDF trajectory {self.filename} does not contain "
                      f"frame information")
            raise ValueError(errmsg) from None

        try:
            self.remarks = self.trjfile.title
        except AttributeError:
            self.remarks = ""
        # other metadata (*= requd):
        # - application           AMBER
        #

        # checks for not-implemented features (other units would need to be
        # hacked into MDAnalysis.units)
        self._verify_units(self.trjfile.variables['time'].units, 'picosecond')
        self._verify_units(self.trjfile.variables['coordinates'].units,
                           'angstrom')

        # Check for scale_factor attributes for all data variables and
        # store this to multiply through later (Issue #2323)
        self.scale_factors = {'time': None,
                              'cell_lengths': None,
                              'cell_angles': None,
                              'coordinates': None,
                              'velocities': None,
                              'forces': None}

        for variable in self.trjfile.variables:
            if hasattr(self.trjfile.variables[variable], 'scale_factor'):
                if variable in self.scale_factors:
                    scale_factor = self.trjfile.variables[variable].scale_factor
                    if not isinstance(scale_factor, (float, np.floating)):
                        raise TypeError(f"{scale_factor} is not a float")
                    self.scale_factors[variable] = scale_factor
                else:
                    errmsg = ("scale_factors for variable {0} are "
                              "not implemented".format(variable))
                    raise NotImplementedError(errmsg)

        self.has_velocities = 'velocities' in self.trjfile.variables
        if self.has_velocities:
            self._verify_units(self.trjfile.variables['velocities'].units,
                               'angstrom/picosecond')

        self.has_forces = 'forces' in self.trjfile.variables
        if self.has_forces:
            self._verify_units(self.trjfile.variables['forces'].units,
                               'kilocalorie/mole/angstrom')

        self.periodic = 'cell_lengths' in self.trjfile.variables
        if self.periodic:
            self._verify_units(self.trjfile.variables['cell_lengths'].units,
                               'angstrom')
            # As of v1.0.0 only `degree` is accepted as a unit
            cell_angle_units = self.trjfile.variables['cell_angles'].units
            self._verify_units(cell_angle_units, 'degree')

        self._current_frame = 0

        self.ts = self._Timestep(self.n_atoms,
                                 velocities=self.has_velocities,
                                 forces=self.has_forces,
                                 reader=self,  # for dt
                                 **self._ts_kwargs)

        # load first data frame
        self._read_frame(0)

    @staticmethod
    def _verify_units(eval_unit, expected_units):
        if eval_unit.decode('utf-8') != expected_units:
            errmsg = ("NETCDFReader currently assumes that the trajectory "
                      "was written in units of {0} instead of {1}".format(
                       eval_unit.decode('utf-8'), expected_units))
            raise NotImplementedError(errmsg)

    @staticmethod
    def parse_n_atoms(filename, **kwargs):
        with scipy.io.netcdf_file(filename, mmap=None) as f:
            n_atoms = f.dimensions['atom']
        return n_atoms

    def _get_var_and_scale(self, variable, frame):
        """Helper function to get variable at given frame from NETCDF file and
        scale if necessary.

        Note
        ----
        If scale_factor is 1.0 within numerical precision then we don't apply
        the scaling.
        """
        scale_factor = self.scale_factors[variable]
        if scale_factor is None or isclose(scale_factor, 1):
            return self.trjfile.variables[variable][frame]
        else:
            return self.trjfile.variables[variable][frame] * scale_factor

    def _read_frame(self, frame):
        ts = self.ts

        if self.trjfile is None:
            raise IOError("Trajectory is closed")
        if np.dtype(type(frame)) != np.dtype(int):
            # convention... for netcdf could also be a slice
            raise TypeError("frame must be a positive integer or zero")
        if frame >= self.n_frames or frame < 0:
            raise IndexError("frame index must be 0 <= frame < {0}".format(
                self.n_frames))
        # note: self.trjfile.variables['coordinates'].shape == (frames, n_atoms, 3)
        ts._pos[:] = self._get_var_and_scale('coordinates', frame)
        ts.time = self._get_var_and_scale('time', frame)
        if self.has_velocities:
            ts._velocities[:] = self._get_var_and_scale('velocities', frame)
        if self.has_forces:
            ts._forces[:] = self._get_var_and_scale('forces', frame)
        if self.periodic:
            unitcell = np.zeros(6)
            unitcell[:3] = self._get_var_and_scale('cell_lengths', frame)
            unitcell[3:] = self._get_var_and_scale('cell_angles', frame)
            ts.dimensions = unitcell
        if self.convert_units:
            self.convert_pos_from_native(ts._pos)  # in-place !
            self.convert_time_from_native(
                ts.time)  # in-place ! (hope this works...)
            if self.has_velocities:
                self.convert_velocities_from_native(ts._velocities,
                                                    inplace=True)
            if self.has_forces:
                self.convert_forces_from_native(ts._forces, inplace=True)
            if self.periodic:
                # in-place ! (only lengths)
                self.convert_pos_from_native(ts.dimensions[:3])
        ts.frame = frame  # frame labels are 0-based
        self._current_frame = frame
        return ts

    def _reopen(self):
        self._current_frame = -1

    def _read_next_timestep(self, ts=None):
        if ts is None:
            ts = self.ts
        try:
            return self._read_frame(self._current_frame + 1)
        except IndexError:
            raise IOError from None

    def _get_dt(self):
        """Gets dt based on the time difference between the first and second
        frame. If missing (i.e. an IndexError is triggered), raises an
        AttributeError which triggers the default 1 ps return of dt().
        """
        try:
            t1 = self.trjfile.variables['time'][1]
            t0 = self.trjfile.variables['time'][0]
        except IndexError:
            raise AttributeError
        return t1 - t0

    def close(self):
        """Close trajectory; any further access will raise an :exc:`IOError`.

        .. Note:: The underlying :mod:`scipy.io.netcdf` module may open netcdf
                  files with :class:`~mmap.mmap` if ``mmap=True`` was
                  set. Hence *any* reference to an array *must* be removed
                  before the file can be closed.

        """
        if self.trjfile is not None:
            self.trjfile.close()
            self.trjfile = None

    def Writer(self, filename, **kwargs):
        """Returns a NCDFWriter for `filename` with the same parameters as this NCDF.

        All values can be changed through keyword arguments.

        Parameters
        ----------
        filename : str
            filename of the output NCDF trajectory
        n_atoms : int (optional)
            number of atoms
        remarks : str (optional)
            string that is stored in the title field
        convert_units : bool (optional)
            ``True``: units are converted to the AMBER base format
        velocities : bool (optional)
            Write velocities into the trajectory
        forces : bool (optional)
            Write forces into the trajectory
        scale_time : float (optional)
            Scale factor for time units
        scale_cell_lengths : float (optional)
            Scale factor for cell lengths
        scale_cell_angles : float (optional)
            Scale factor for cell angles
        scale_coordinates : float (optional)
            Scale factor for coordinates
        scale_velocities : float (optional)
            Scale factor for velocities
        scale_forces : float (optional)
            Scale factor for forces

        Returns
        -------
        :class:`NCDFWriter`
        """
        n_atoms = kwargs.pop('n_atoms', self.n_atoms)
        kwargs.setdefault('remarks', self.remarks)
        kwargs.setdefault('convert_units', self.convert_units)
        kwargs.setdefault('velocities', self.has_velocities)
        kwargs.setdefault('forces', self.has_forces)
        for key in self.scale_factors:
            kwargs.setdefault(f'scale_{key}', self.scale_factors[key])
        return NCDFWriter(filename, n_atoms, **kwargs)


class NCDFWriter(base.WriterBase):
    """Writer for `AMBER NETCDF format`_ (version 1.0).

    AMBER binary trajectories are automatically recognised by the
    file extension ".ncdf" or ".nc".

    Velocities are written out if they are detected in the input
    :class:`Timestep`. The trajectories are always written with ångström
    for the lengths and picoseconds for the time (and hence Å/ps for
    velocities and kilocalorie/mole/Å for forces).

    Scale factor variables for time, velocities, cell lengths, cell angles,
    coordinates, velocities, or forces can be passed into the writer. If so,
    they will be written to the NetCDF file. In this case, the trajectory data
    will be written to the NetCDF file divided by the scale factor value. By
    default, scale factor variables are not written. The only exception is for
    velocities, where it is set to 20.455, replicating the default behaviour of
    AMBER.

    Unit cell information is written if available.

    .. _AMBER NETCDF format: http://ambermd.org/netcdf/nctraj.xhtml


    Parameters
    ----------
    filename : str
        name of output file
    n_atoms : int
        number of atoms in trajectory file
    convert_units : bool (optional)
        ``True``: units are converted to the AMBER base format; [``True``]
    velocities : bool (optional)
        Write velocities into the trajectory [``False``]
    forces : bool (optional)
        Write forces into the trajectory [``False``]
    scale_time : float (optional)
        Scale factor for time units [`None`]
    scale_cell_lengths : float (optional)
        Scale factor for cell lengths [``None``]
    scale_cell_angles : float (optional)
        Scale factor for cell angles [``None``]
    scale_coordinates : float (optional)
        Scale factor for coordinates [``None``]
    scale_velocities : float (optional)
        Scale factor for velocities [20.455]
    scale_forces : float (optional)
        Scale factor for forces [``None``]


    Note
    ----
    MDAnalysis uses :mod:`scipy.io.netcdf` to access AMBER files, which are in
    netcdf 3 format. Although :mod:`scipy.io.netcdf` is very fast at reading
    these files, it is *very* slow when writing, and it becomes slower the
    longer the files are. On the other hand, the netCDF4_ package (which
    requires the compiled netcdf library to be installed) is fast at writing
    but slow at reading. Therefore, we try to use :mod:`netCDF4` for writing if
    available but otherwise fall back to the slower :mod:`scipy.io.netcdf`.

    **AMBER users** might have a hard time getting netCDF4 to work with a
    conda-based installation (as discussed in `Issue #506`_) because of the way
    that AMBER itself handles netcdf: When the AMBER environment is loaded, the
    following can happen when trying to import netCDF4::

      >>> import netCDF4
      Traceback (most recent call last):
        File "<string>", line 1, in <module>
        File "/scratch2/miniconda/envs/py35/lib/python3.5/site-packages/netCDF4/__init__.py", line 3, in <module>
          from ._netCDF4 import *
      ImportError: /scratch2/miniconda/envs/py35/lib/python3.5/site-packages/netCDF4/_netCDF4.cpython-35m-x86_64-linux-gnu.so: undefined symbol: nc_inq_var_fletcher32

    The reason for this (figured out via :program:`ldd`) is that AMBER builds
    its own NetCDF library that it now inserts into :envvar:`LD_LIBRARY_PATH`
    *without the NetCDF4 API and HDF5 bindings*. Since the conda version of
    :mod:`netCDF4` was built against the full NetCDF package, the one
    :program:`ld` tries to link to at runtime (because AMBER requires
    :envvar:`LD_LIBRARY_PATH`) is missing some symbols. Removing AMBER from the
    environment fixes the import but is not really a convenient solution for
    users of AMBER.

    At the moment there is no obvious solution if one wants to use
    :mod:`netCDF4` and AMBER in the same shell session. If you need the fast
    writing capabilities of :mod:`netCDF4` then you need to unload your AMBER
    environment before importing MDAnalysis.


    .. _netCDF4: https://unidata.github.io/netcdf4-python/
    .. _`Issue #506`:
       https://github.com/MDAnalysis/mdanalysis/issues/506#issuecomment-225081416

    Raises
    ------
    FutureWarning
        When writing. The :class:`NCDFWriter` currently does not write any
        `scale_factor` values for the data variables. Whilst not in breach
        of the AMBER NetCDF standard, this behaviour differs from that of
        most AMBER writers, especially for velocities which usually have a
        `scale_factor` of 20.455. In MDAnalysis 2.0, the :class:`NCDFWriter`
        will enforce `scale_factor` writing to either match user inputs (either
        manually defined or via the :class:`NCDFReader`) or those as written by
        AmberTools's :program:`sander` under default operation.

    See Also
    --------
    :class:`NCDFReader`


    .. versionadded: 0.7.6
    .. versionchanged:: 0.10.0
       Added ability to write velocities and forces
    .. versionchanged:: 0.11.0
       kwarg `delta` renamed to `dt`, for uniformity with other Readers
    .. versionchanged:: 0.17.0
       Use fast :mod:`netCDF4` for writing but fall back to slow
       :mod:`scipy.io.netcdf` if :mod:`netCDF4` is not available.
    .. versionchanged:: 0.20.1
       Changes the `cell_angles` unit to the AMBER NetCDF convention standard
       of `degree` instead of the `degrees` written in previous version of
       MDAnalysis (Issue #2327).
    .. versionchanged:: 2.0.0
       ``dt``, ``start``, and ``step`` keywords were unused and are no longer
       set.
       Writing of ``scale_factor`` values has now been implemented. By default
       only velocities write a scale_factor of 20.455 (echoing the behaviour
       seen from AMBER).

    """

    format = ['NC', 'NCDF']
    multiframe = True
    version = "1.0"
    units = {'time': 'ps',
             'length': 'Angstrom',
             'velocity': 'Angstrom/ps',
             'force': 'kcal/(mol*Angstrom)'}

    def __init__(self, filename, n_atoms, remarks=None, convert_units=True,
                 velocities=False, forces=False, scale_time=None,
                 scale_cell_lengths=None, scale_cell_angles=None,
                 scale_coordinates=None, scale_velocities=None,
                 scale_forces=None, **kwargs):
        self.filename = filename
        if n_atoms == 0:
            raise ValueError("NCDFWriter: no atoms in output trajectory")
        self.n_atoms = n_atoms
        # convert length and time to base units on the fly?
        self.convert_units = convert_units

        self.remarks = remarks or "AMBER NetCDF format (MDAnalysis.coordinates.trj.NCDFWriter)"

        self._first_frame = True  # signals to open trajectory
        self.trjfile = None  # open on first write with _init_netcdf()
        self.periodic = None  # detect on first write
        self.has_velocities = velocities
        self.has_forces = forces

        self.scale_factors = {
                'time': scale_time,
                'cell_lengths': scale_cell_lengths,
                'cell_angles': scale_cell_angles,
                'coordinates': scale_coordinates,
                'velocities': scale_velocities,
                'forces': scale_forces}
        # NCDF standard enforces float scale_factors
        for value in self.scale_factors.values():
            if (value is not None) and (
                    not isinstance(value, (float, np.floating))):
                errmsg = f"scale_factor {value} is not a float"
                raise TypeError(errmsg)

        self.curr_frame = 0

    def _init_netcdf(self, periodic=True):
        """Initialize netcdf AMBER 1.0 trajectory.

        The trajectory is opened when the first frame is written
        because that is the earliest time that we can detect if the
        output should contain periodicity information (i.e. the unit
        cell dimensions).

        Based on Joshua Adelman's `netcdf4storage.py`_ in `Issue 109`_ and uses
        Jason Swail's hack from `ParmEd/ParmEd#722`_ to switch between
        :mod:`scipy.io.netcdf` and :mod:`netCDF4`.

        .. _`Issue 109`:
           https://github.com/MDAnalysis/mdanalysis/issues/109
        .. _`netcdf4storage.py`:
           https://storage.googleapis.com/google-code-attachments/mdanalysis/issue-109/comment-2/netcdf4storage.py
        .. _`ParmEd/ParmEd#722`: https://github.com/ParmEd/ParmEd/pull/722

        """
        if not self._first_frame:
            raise IOError(
                errno.EIO,
                "Attempt to write to closed file {0}".format(self.filename))

        if netCDF4:
            ncfile = netCDF4.Dataset(self.filename, 'w',
                                     format='NETCDF3_64BIT')
        else:
            ncfile = scipy.io.netcdf_file(self.filename,
                                          mode='w', version=2,
                                          maskandscale=False)
            wmsg = ("Could not find netCDF4 module. Falling back to MUCH "
                    "slower scipy.io.netcdf implementation for writing.")
            logger.warning(wmsg)
            warnings.warn(wmsg)

        # Set global attributes.
        setattr(ncfile, 'program', 'MDAnalysis.coordinates.TRJ.NCDFWriter')
        setattr(ncfile, 'programVersion', MDAnalysis.__version__)
        setattr(ncfile, 'Conventions', 'AMBER')
        setattr(ncfile, 'ConventionVersion', '1.0')
        setattr(ncfile, 'application', 'MDAnalysis')

        # Create dimensions
        ncfile.createDimension('frame',
                               None)  # unlimited number of steps (can append)
        ncfile.createDimension('atom',
                               self.n_atoms)  # number of atoms in system
        ncfile.createDimension('spatial', 3)  # number of spatial dimensions
        ncfile.createDimension('cell_spatial', 3)  # unitcell lengths
        ncfile.createDimension('cell_angular', 3)  # unitcell angles
        ncfile.createDimension('label', 5)  # needed for cell_angular

        # Create variables.
        coords = ncfile.createVariable('coordinates', 'f4',
                                       ('frame', 'atom', 'spatial'))
        setattr(coords, 'units', 'angstrom')
        if self.scale_factors['coordinates']:
            coords.scale_factor = self.scale_factors['coordinates']

        spatial = ncfile.createVariable('spatial', 'c', ('spatial', ))
        spatial[:] = np.asarray(list('xyz'))

        time = ncfile.createVariable('time', 'f4', ('frame',))
        setattr(time, 'units', 'picosecond')
        if self.scale_factors['time']:
            time.scale_factor = self.scale_factors['time']

        self.periodic = periodic
        if self.periodic:
            cell_lengths = ncfile.createVariable('cell_lengths', 'f8',
                                                 ('frame', 'cell_spatial'))
            setattr(cell_lengths, 'units', 'angstrom')
            if self.scale_factors['cell_lengths']:
                cell_lengths.scale_factor = self.scale_factors['cell_lengths']

            cell_spatial = ncfile.createVariable('cell_spatial', 'c',
                                                 ('cell_spatial', ))
            cell_spatial[:] = np.asarray(list('abc'))

            cell_angles = ncfile.createVariable('cell_angles', 'f8',
                                                ('frame', 'cell_angular'))
            setattr(cell_angles, 'units', 'degree')
            if self.scale_factors['cell_angles']:
                cell_angles.scale_factor = self.scale_factors['cell_angles']

            cell_angular = ncfile.createVariable('cell_angular', 'c',
                                                 ('cell_angular', 'label'))
            cell_angular[:] = np.asarray([list('alpha'), list('beta '), list(
                'gamma')])

        # These properties are optional, and are specified on Writer creation
        if self.has_velocities:
            velocs = ncfile.createVariable('velocities', 'f4',
                                           ('frame', 'atom', 'spatial'))
            setattr(velocs, 'units', 'angstrom/picosecond')
            if self.scale_factors['velocities']:
                velocs.scale_factor = self.scale_factors['velocities']
        if self.has_forces:
            forces = ncfile.createVariable('forces', 'f4',
                                           ('frame', 'atom', 'spatial'))
            setattr(forces, 'units', 'kilocalorie/mole/angstrom')
            if self.scale_factors['forces']:
                forces.scale_factor = self.scale_factors['forces']

        # Important for netCDF4! Disable maskandscale for created variables!
        # Won't work if called before variable creation!
        if netCDF4:
            ncfile.set_auto_maskandscale(False)

        ncfile.sync()
        self._first_frame = False
        self.trjfile = ncfile

    def is_periodic(self, ts):
        """Test if timestep ``ts`` contains a periodic box.

        Parameters
        ----------
        ts : :class:`Timestep`
             :class:`Timestep` instance containing coordinates to
             be written to trajectory file

        Returns
        -------
        bool
            Return ``True`` if `ts` contains a valid simulation box
        """
        return ts.dimensions is not None

    def _write_next_frame(self, ag):
        """Write information associated with ``ag`` at current frame into trajectory

        Parameters
        ----------
        ag : AtomGroup or Universe


        .. versionchanged:: 1.0.0
           Added ability to use either AtomGroup or Universe.
           Renamed from `write_next_timestep` to `_write_next_frame`.
        .. versionchanged:: 2.0.0
           Deprecated support for Timestep argument has now been removed.
           Use AtomGroup or Universe as an input instead.
        """
        try:
            # Atomgroup?
            ts = ag.ts
        except AttributeError:
            try:
                # Universe?
                ts = ag.trajectory.ts
            except AttributeError:
                errmsg = "Input obj is neither an AtomGroup or Universe"
                raise TypeError(errmsg) from None

        if ts.n_atoms != self.n_atoms:
            raise IOError(
                "NCDFWriter: Timestep does not have the correct number of atoms")

        if self.trjfile is None:
            # first time step: analyze data and open trajectory accordingly
            self._init_netcdf(periodic=self.is_periodic(ts))

        return self._write_next_timestep(ts)

    def _set_frame_var_and_scale(self, varname, data):
        """Helper function to set variables and scale them if necessary.

        Note
        ----
        If scale_factor is numerically close to 1.0, the variable data is not
        scaled.
        """
        sfactor = self.scale_factors[varname]
        if sfactor is None or isclose(sfactor, 1):
            self.trjfile.variables[varname][self.curr_frame] = data
        else:
            self.trjfile.variables[varname][self.curr_frame] = data / sfactor

    def _write_next_timestep(self, ts):
        """Write coordinates and unitcell information to NCDF file.

        Do not call this method directly; instead use
        :meth:`write` because some essential setup is done
        there before writing the first frame.

        Based on Joshua Adelman's `netcdf4storage.py`_ in `Issue 109`_.

        .. _`Issue 109`:
           https://github.com/MDAnalysis/mdanalysis/issues/109
        .. _`netcdf4storage.py`:
           https://storage.googleapis.com/google-code-attachments/mdanalysis/issue-109/comment-2/netcdf4storage.py


        .. versionchanged:: 2.0.0
           Can now write scale_factors, and scale variables accordingly.
        """
        pos = ts._pos
        time = ts.time
        unitcell = ts.dimensions

        if self.convert_units:
            # make a copy of the scaled positions so that the in-memory
            # timestep is not changed (would have lead to wrong results if
            # analysed *after* writing a time step to disk). The new
            # implementation could lead to memory problems and/or slow-down for
            # very big systems because we temporarily create a new array pos
            # for each frame written
            pos = self.convert_pos_to_native(pos, inplace=False)
            time = self.convert_time_to_native(time, inplace=False)
            unitcell = self.convert_dimensions_to_unitcell(ts)

        # write step
        # coordinates
        self._set_frame_var_and_scale('coordinates', pos)

        # time
        self._set_frame_var_and_scale('time', time)

        # unitcell
        if self.periodic:
            # cell lengths
            self._set_frame_var_and_scale('cell_lengths', unitcell[:3])

            self._set_frame_var_and_scale('cell_angles', unitcell[3:])

        # velocities
        if self.has_velocities:
            velocities = ts._velocities
            if self.convert_units:
                velocities = self.convert_velocities_to_native(
                    velocities, inplace=False)

            self._set_frame_var_and_scale('velocities', velocities)

        # forces
        if self.has_forces:
            forces = ts._forces
            if self.convert_units:
                forces = self.convert_forces_to_native(
                    forces, inplace=False)

            self._set_frame_var_and_scale('forces', forces)

        self.trjfile.sync()
        self.curr_frame += 1

    def close(self):
        if self.trjfile is not None:
            self.trjfile.close()
            self.trjfile = None


class NCDFPicklable(scipy.io.netcdf_file):
    """NetCDF file object (read-only) that can be pickled.

    This class provides a file-like object (as returned by
    :class:`scipy.io.netcdf_file`) that,
    unlike standard Python file objects,
    can be pickled. Only read mode is supported.

    When the file is pickled, filename and mmap of the open file handle in
    the file are saved. On unpickling, the file is opened by filename,
    and the mmap file is loaded.
    This means that for a successful unpickle, the original file still has to
    be accessible with its filename.


    .. note::
        This class subclasses :class:`scipy.io.netcdf_file`, please
        see the `scipy netcdf API documentation`_ for more information on
        the parameters and how the class behaviour.


    Parameters
    ----------
    filename : str or file-like
        a filename given a text or byte string.
    mmap : None or bool, optional
        Whether to mmap `filename` when reading. True when `filename`
        is a file name, False when `filename` is a file-like object.
    version : {1, 2}, optional
        Sets the netcdf file version to read / write. 1 is classic, 2 is
        64-bit offset format. Default is 1 (but note AMBER ncdf *requires* 2).
    maskandscale : bool, optional
        Whether to automatically scale and mask data. Default is False.

    Example
    -------
    ::

        f = NCDFPicklable(NCDF)
        print(f.variables['coordinates'].data)
        f.close()

    can also be used as context manager::

        with NCDFPicklable(NCDF) as f:
            print(f.variables['coordinates'].data)

    See Also
    ---------
    :class:`MDAnalysis.lib.picklable_file_io.FileIOPicklable`
    :class:`MDAnalysis.lib.picklable_file_io.BufferIOPicklable`
    :class:`MDAnalysis.lib.picklable_file_io.TextIOPicklable`
    :class:`MDAnalysis.lib.picklable_file_io.GzipPicklable`
    :class:`MDAnalysis.lib.picklable_file_io.BZ2Picklable`


    .. versionadded:: 2.0.0


    .. _`scipy netcdf API documentation`: https://docs.scipy.org/doc/scipy/reference/generated/scipy.io.netcdf_file.html
    """
    def __getstate__(self):
        return (self.filename, self.use_mmap, self.version_byte,
                self.maskandscale)

    def __setstate__(self, args):
        self.__init__(args[0], mmap=args[1], version=args[2],
                      maskandscale=args[3])
