"""The one place that knows how clocks relate.

``contract`` names the clocks and who owns each, ``producer`` turns a
plugin's source times into LSL times, and ``assessment`` compares recorded
times for the live recording barriers and the offline validation alike.
Pure standard library plus optional numpy, so the host, the recording worker
and every plugin process can import it.
"""
