"""Parses Rinex files.

The Ephemeris Manager provides broadcast ephemeris for specific
satellites at a specific timestep. The EphemerisDownloader class should be
initialized and then the ``get_ephemeris`` function can be used to
retrieve ephemeris for specific satellites. ``get_ephemeris`` returns
the most recent broadcast ephemeris for the provided list of satellites
that was broadcast BEFORE the provided timestamp. For example GPS daily
ephemeris files contain data at a two hour frequency, so if the
timestamp provided is 5am, then ``get_ephemeris`` will return the 4am
data but not 6am. If provided a timestamp between midnight and 2am then
the ephemeris from around midnight (might be the day before) will be
provided. If no list of satellites is provided, then ``get_ephemeris``
will return data for all satellites.

When multiple observations are provided for the same satellite and same
timestep, the Ephemeris Manager will only return the first instance.
This is applicable when requesting ephemeris for multi-GNSS for the
current day. Same-day multi GNSS data is pulled from  same day. For
same-day multi-GNSS from https://igs.org/data/ which often has multiple
observations.

"""


__authors__ = "Ashwin Kanhere, Shubh Gupta"
__date__ = "13 July 2021"

import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import georinex as gr

import gnss_lib_py.utils.constants as consts
from gnss_lib_py.parsers.navdata import NavData
from gnss_lib_py.utils.time_conversions import datetime_to_gps_millis
from gnss_lib_py.utils.ephemeris_downloader import EphemerisDownloader, DEFAULT_EPHEM_PATH


class Rinex(NavData):
    """Class to handle Rinex measurements.

    The Ephemeris Manager provides broadcast ephemeris for specific
    satellites at a specific timestep. The EphemerisDownloader class
    should be initialized and then the ``get_ephemeris`` function
    can be used to retrieve ephemeris for specific satellites.
    ``get_ephemeris`` returns the most recent broadcast ephemeris
    for the provided list of satellites that was broadcast BEFORE
    the provided timestamp. For example GPS daily ephemeris files
    contain data at a two hour frequency, so if the timestamp
    provided is 5am, then ``get_ephemeris`` will return the 4am data
    but not 6am. If provided a timestamp between midnight and 2am
    then the ephemeris from around midnight (might be the day
    before) will be provided. If no list of satellites is provided,
    then ``get_ephemeris`` will return data for all satellites.

    When multiple observations are provided for the same satellite
    and same timestep, the Ephemeris Manager will only return the
    first instance. This is applicable when requesting ephemeris for
    multi-GNSS for the current day. Same-day multi GNSS data is
    pulled from  same day. For same-day multi-GNSS from
    https://igs.org/data/ which often has multiple observations.

    Inherits from NavData().

    Attributes
    ----------
    iono_params : np.ndarray
        Array of ionosphere parameters ION ALPHA and ION BETA
    verbose : bool
        If true, prints debugging statements.

    """

    def __init__(self, input_paths, satellites=None, verbose=False):
        """Rinex specific loading and preprocessing

        Parameters
        ----------
        input_paths : string or path-like or list of paths
            Path to measurement Rinex file(s).
        satellites : List
            List of satellite IDs as a string, for example ['G01','E11',
            'R06']. Defaults to None which returns get_ephemeris for
            all satellites.

        """
        self.iono_params = None
        self.verbose = verbose
        pd_df = self.preprocess(input_paths, satellites)

        super().__init__(pandas_df=pd_df)


    def preprocess(self, rinex_paths, satellites):
        """Combine Rinex files and create pandas frame if necessary.

        Parameters
        ----------
        rinex_paths : string or path-like or list of paths
            Path to measurement Rinex file(s).
        satellites : List
            List of satellite IDs as a string, for example ['G01','E11',
            'R06']. Defaults to None which returns get_ephemeris for
            all satellites.

        Returns
        -------
        data : pd.DataFrame
            Combined rinex data from all files.

        """

        constellations = EphemerisDownloader.get_constellations(satellites)

        if isinstance(rinex_paths, (str, os.PathLike)):
            rinex_paths = [rinex_paths]

        data = pd.DataFrame()
        self.iono_params = []
        for rinex_path in rinex_paths:
            new_data = self._get_ephemeris_dataframe(rinex_path,
                                                     constellations)
            data = pd.concat((data,new_data), ignore_index=True)
            self.iono_params.append(self.get_iono_params(rinex_path))
        data.reset_index(inplace=True, drop=True)
        data.sort_values('time', inplace=True, ignore_index=True)

        if satellites is not None:
            data = data.loc[data['sv'].isin(satellites)]

        # Move sv to DataFrame columns, reset index
        data = data.reset_index(drop=True)
        # Replace datetime with gps_millis
        gps_millis = [np.float64(datetime_to_gps_millis(df_row['time'])) \
                        for _, df_row in data.iterrows()]
        data['gps_millis'] = gps_millis
        data = data.drop(columns=['time'])
        data = data.rename(columns={"sv":"sv_id"})
        if "GPSWeek" in data.columns:
            data = data.rename(columns={"GPSWeek":"gps_week"})
            if "GALWeek" in data.columns:
                data["gps_week"] = np.where(pd.isnull(data["gps_week"]),
                                                      data["GALWeek"],
                                                      data["gps_week"])
        elif "GALWeek" in data.columns:
            data = data.rename(columns={"GALWeek":"gps_week"})
        if len(data) == 0:
            raise RuntimeError("No ephemeris data available for the " \
                             + "given satellites")
        return data

    def postprocess(self):
        """Rinex specific post processing.

        """

        self['gnss_sv_id'] = self['sv_id']
        gnss_chars = [sv_id[0] for sv_id in np.atleast_1d(self['sv_id'])]
        gnss_nums = [sv_id[1:] for sv_id in np.atleast_1d(self['sv_id'])]
        gnss_id = [consts.CONSTELLATION_CHARS[gnss_char] for gnss_char in gnss_chars]
        self['gnss_id'] = np.asarray(gnss_id)
        self['sv_id'] = np.asarray(gnss_nums, dtype=int)

    def _get_ephemeris_dataframe(self, rinex_path, constellations=None):
        """Load/download ephemeris files and process into DataFrame

        Parameters
        ----------
        rinex_path : string or path-like
            Filepath to rinex file

        constellations : Set
            Set of satellites {"ConstIDSVID"}

        Returns
        -------
        data : pd.DataFrame
            Parsed ephemeris DataFrame
        """

        if constellations is not None:
            data = gr.load(rinex_path,
                                 use=constellations,
                                 verbose=self.verbose).to_dataframe()
        else:
            data = gr.load(rinex_path,
                                 verbose=self.verbose).to_dataframe()

        leap_seconds = self.load_leapseconds(rinex_path)
        if leap_seconds is None:
            data['leap_seconds'] = np.nan
        else:
            data['leap_seconds'] = leap_seconds
        data.dropna(how='all', inplace=True)
        data.reset_index(inplace=True)
        data['source'] = rinex_path
        data['t_oc'] = pd.to_numeric(data['time'] - datetime(1980, 1, 6, 0, 0, 0))
        #TODO: Use a constant for the time of GPS clock start
        data['t_oc']  = 1e-9 * data['t_oc'] - consts.WEEKSEC * np.floor(1e-9 * data['t_oc'] / consts.WEEKSEC)
        data['time'] = data['time'].dt.tz_localize('UTC')
        data.rename(columns={'M0': 'M_0', 'Eccentricity': 'e', 'Toe': 't_oe', 'DeltaN': 'deltaN', 'Cuc': 'C_uc', 'Cus': 'C_us',
                             'Cic': 'C_ic', 'Crc': 'C_rc', 'Cis': 'C_is', 'Crs': 'C_rs', 'Io': 'i_0', 'Omega0': 'Omega_0'}, inplace=True)

        return data

    def get_iono_params(self, rinex_path):
        """Gets ionosphere parameters from RINEX file header for calculation of
        ionosphere delay

        Parameters
        ----------
        rinex_path : string or path-like
            Filepath to rinex file

        Returns
        -------
        iono_params : np.ndarray
            Array of ionosphere parameters ION ALPHA and ION BETA
        """
        try:
            ion_alpha_str = gr.rinexheader(rinex_path)['ION ALPHA'].replace('D', 'E')
            ion_alpha = np.array(list(map(float, ion_alpha_str.split())))
        except KeyError:
            ion_alpha = np.array([[np.nan]])
        try:
            ion_beta_str = gr.rinexheader(rinex_path)['ION BETA'].replace('D', 'E')
            ion_beta = np.array(list(map(float, ion_beta_str.split())))
        except KeyError:
            ion_beta = np.array([[np.nan]])
        iono_params = np.vstack((ion_alpha, ion_beta))

        return iono_params

    def load_leapseconds(self, filename):
        """Read leapseconds from rinex file

        Parameters
        ----------
        filename : string
            Ephemeris filename

        Returns
        -------
        read_lp_sec : int or None
            Leap seconds read from file

        """
        with open(filename) as f:
            for line in f:
                if 'LEAP SECONDS' in line:
                    read_lp_sec = int(line.split()[0])
                    return read_lp_sec
                if 'END OF HEADER' in line:
                    return None

        return None

def get_time_cropped_rinex(timestamp, satellites=None,
                           ephemeris_directory=DEFAULT_EPHEM_PATH):
    """Add SV states using Rinex file.

    Provides broadcast ephemeris for specific
    satellites at a specific timestep
    ``add_sv_states_rinex`` returns the most recent broadcast ephemeris
    for the provided list of satellites that was broadcast BEFORE
    the provided timestamp. For example GPS daily ephemeris files
    contain data at a two hour frequency, so if the timestamp
    provided is 5am, then ``add_sv_states_rinex`` will return the 4am data
    but not 6am. If provided a timestamp between midnight and 2am
    then the ephemeris from around midnight (might be the day
    before) will be provided. If no list of satellites is provided,
    then ``add_sv_states_rinex`` will return data for all satellites.

    When multiple observations are provided for the same satellite
    and same timestep,  will only return the
    first instance. This is applicable when requesting ephemeris for
    multi-GNSS for the current day. Same-day multi GNSS data is
    pulled from  same day. For same-day multi-GNSS from
    https://igs.org/data/ which often has multiple observations.

    Parameters
    ----------
    timestamp : datetime.datetime
        Ephemeris data is returned for the timestamp day and
        includes all broadcast ephemeris whose broadcast timestamps
        happen before the given timestamp variable. Timezone should
        be added manually and is interpreted as UTC if not added.
    satellites : List
        List of satellite IDs as a string, for example ['G01','E11',
        'R06']. Defaults to None which returns get_ephemeris for
        all satellites.

    Returns
    -------
    data : gnss_lib_py.parsers.navdata.NavData
        ephemeris entries corresponding to timestamp

    Notes
    -----
    The Galileo week ``GALWeek`` is identical to the GPS Week
    ``GPSWeek``. See http://acc.igs.org/misc/rinex304.pdf page A26

    """

    ephemeris_downloader = EphemerisDownloader(ephemeris_directory)
    rinex_paths = ephemeris_downloader.get_ephemeris(timestamp,satellites)
    rinex_data = Rinex(rinex_paths, satellites=satellites)

    timestamp_millis = datetime_to_gps_millis(timestamp)
    time_cropped_data = rinex_data.where('gps_millis', timestamp_millis, "lesser")

    time_cropped_data = time_cropped_data.pandas_df().sort_values(
        'gps_millis').groupby('gnss_sv_id').last()
    if satellites is not None and len(time_cropped_data) < len(satellites):
        # if no data available for the given day, try looking at the
        # previous day, may occur when a time near to midnight
        # is provided. For example, 12:01am
        if len(time_cropped_data) != 0:
            satellites = list(set(satellites) - set(time_cropped_data.index))
        prev_day_timestamp = datetime(year=timestamp.year,
                                      month=timestamp.month,
                                      day=timestamp.day - 1,
                                      hour=23,
                                      minute=59,
                                      second=59,
                                      microsecond=999999,
                                      tzinfo=timezone.utc,
                                      )
        prev_rinex_paths = ephemeris_downloader.get_ephemeris(prev_day_timestamp,
                                                              satellites)
        # TODO: verify that the above statement doesn't need "False for timestamp"
        prev_rinex_data = Rinex(prev_rinex_paths, satellites=satellites)

        prev_data = prev_rinex_data.pandas_df().sort_values('gps_millis').groupby(
            'gnss_sv_id').last()
        rinex_data_df = pd.concat((time_cropped_data,prev_data))
        rinex_iono_params = prev_rinex_data.iono_params + rinex_data.iono_params
    else:
        rinex_data_df = time_cropped_data
        rinex_iono_params = rinex_data.iono_params

    rinex_data_df = rinex_data_df.reset_index()
    rinex_data = NavData(pandas_df=rinex_data_df)
    rinex_data.iono_params = rinex_iono_params

    return rinex_data


class RinexObs3(NavData):
    """Class handling Rinex 3 observation files [1]_.

    The Rinex Observation files (of the format .yyo) contain measured
    pseudoranges, carrier phase, doppler and signal-to-noise ratio
    measurements for multiple constellations and bands.
    This loader converts those file types into a NavData in which
    measurements from different bands are treated as separate measurement
    instances. Inherits from NavData().


    References
    ----------
    .. [1] https://files.igs.org/pub/data/format/rinex305.pdf


    """

    def __init__(self, input_path):
        """Loading Rinex 3 observation files into a NavData based class.

        Should input path to `.yyo` file.

        Parameters
        ----------
        input_path : string or path-like
            Path to rinex .o file

        """

        obs_file = gr.load(input_path).to_dataframe()
        obs_header = gr.rinexheader(input_path)
        obs_measure_types = obs_header['fields']
        rx_bands = []
        for rx_measures in obs_measure_types.values():
            for single_measure in rx_measures:
                band = single_measure[1]
                if band not in rx_bands:
                    rx_bands.extend(band)

        obs_file.reset_index(inplace=True)
        # Convert time to gps_millis
        gps_millis = [np.float64(datetime_to_gps_millis(df_row['time'])) \
                                for _, df_row in obs_file.iterrows()]
        obs_file['gps_millis'] = gps_millis
        obs_file = obs_file.drop(columns=['time'])
        obs_file = obs_file.rename(columns={"sv":"sv_id"})
        # Convert gnss_sv_id to gnss_id and sv_id (plus gnss_sv_id)
        obs_navdata_raw = NavData(pandas_df=obs_file)
        obs_navdata_raw['gnss_sv_id'] = obs_navdata_raw['sv_id']
        gnss_chars = [sv_id[0] for sv_id in np.atleast_1d(obs_navdata_raw['sv_id'])]
        gnss_nums = [sv_id[1:] for sv_id in np.atleast_1d(obs_navdata_raw['sv_id'])]
        gnss_id = [consts.CONSTELLATION_CHARS[gnss_char] for gnss_char in gnss_chars]
        obs_navdata_raw['gnss_id'] = np.asarray(gnss_id)
        obs_navdata_raw['sv_id'] = np.asarray(gnss_nums, dtype=int)
        # Convert the coded column names to glp standards and extract information
        # into glp row and columns format
        info_rows = ['gps_millis', 'gnss_sv_id', 'sv_id', 'gnss_id']
        super().__init__()
        for band in rx_bands:
            rename_map = {}
            keep_rows = info_rows.copy()
            measure_type_dict = self._measure_type_dict()
            for measure_char, measure_row in measure_type_dict.items():
                measure_band_row = \
                    obs_navdata_raw.find_wildcard_indexes(f'{measure_char}{band}*',
                                                          max_allow=1)
                measure_row_chars = measure_band_row[f'{measure_char}{band}*'][0]
                rename_map[measure_row_chars] = measure_row
                keep_rows.append(measure_row_chars)
            band_navdata = obs_navdata_raw.copy(rows=keep_rows)
            band_navdata.rename(rename_map, inplace=True)
            # Remove the cases with NaNs in the measurements
            for row in rename_map.values():
                band_navdata = band_navdata.where(row, np.nan, 'neq')
            # Assign the gnss_lib_py standard names for signal_type
            rx_constellations = np.unique(band_navdata['gnss_id'])
            signal_type_dict = self._signal_type_dict()
            signal_types = np.empty(len(band_navdata), dtype=object)
            for constellation in rx_constellations:
                signal_type = signal_type_dict[constellation][band]
                signal_types[band_navdata['gnss_id']==constellation] = signal_type
            band_navdata['signal_type'] = signal_types
            if len(self) == 0:
                self.concat(band_navdata, inplace=True)
            else:
                self.concat(band_navdata, inplace=True)
        self.sort('gps_millis', inplace=True)

    @staticmethod
    def _measure_type_dict():
        """Map of Rinex observation measurement types to standard names.

        Returns
        -------
        measure_type_dict : Dict
            Dictionary of the form {rinex_character : measure_name}
        """

        measure_type_dict = {'C': 'raw_pr_m',
                             'L': 'carrier_phase',
                             'D': 'raw_doppler_hz',
                             'S': 'cn0_dbhz'}
        return measure_type_dict

    @staticmethod
    def _signal_type_dict():
        """Dictionary from constellation and signal bands to signal types.

        Returns
        -------
        signal_type_dict : Dict
            Dictionary of the form {constellation_band : {band : signal_type}}
        """
        signal_type_dict = {}
        signal_type_dict['gps'] = {'1' : 'l1',
                                '2' : 'l2',
                                '5' : 'l5'}
        signal_type_dict['glonass'] = {'1' : 'g1',
                                    '4' : 'g1a',
                                    '2' : 'g2',
                                    '6' : 'g2a',
                                    '3' : 'g3'}
        signal_type_dict['galileo'] = {'1' : 'e1',
                                    '5' : 'e5a',
                                    '7' : 'e5b',
                                    '8' : 'e5',
                                    '6' : 'e6'}
        signal_type_dict['sbas'] = {'1' : 'l1',
                                    '5' : 'l5'}
        signal_type_dict['qzss'] = {'1' : 'l1',
                                    '2' : 'l2',
                                    '5' : 'l5',
                                    '6' : 'l6'}
        # beidou needs to be refined because the current level of detail isn't enough
        # to distinguish between different signals
        signal_type_dict['beidou'] = {'2' : 'b1',
                                    '1' : 'b1c',
                                    '5' : 'b2a',
                                    '7' : 'b2b',
                                    '8' : 'b2',
                                    '6' : 'b3'}
        signal_type_dict['irnss'] = {'5' : 'l5',
                                    '9' : 's'}
        return signal_type_dict
